"""
OAuth Authorization Code Flow Handler

Implements BFF OAuth flow using Auth0 as the identity provider.
Google Calendar access uses Auth0 Token Vault (Connected Accounts).

Connected Accounts flow:
  1. POST /me/v1/connected-accounts/connect  → get auth_session + connect_uri/ticket
  2. Redirect user to connect URL
  3. Callback receives connect_code
  4. POST /me/v1/connected-accounts/complete → registers connection in Token Vault
  5. Token retrieval: Federated Token Exchange with main session refresh_token
"""

import os
import json
import secrets
import hashlib
import base64
import time
from typing import Dict, Any, Optional
from urllib.parse import urlencode, quote

import boto3
import requests
from botocore.exceptions import ClientError

from bff_session import (
    create_session,
    validate_session,
    delete_session,
    extract_session_id_from_cookie,
    get_user_context,
    build_session_cookie,
    build_clear_session_cookie,
    update_session_google_connected,
)

# Configuration
AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "violet-hookworm-18506.cic-demo-platform.auth0app.com")
AUTH0_BFF_CLIENT_ID = os.environ.get("AUTH0_BFF_CLIENT_ID", "gYVmHq3MbrI73Uf1Qikb1ze5KfBdDLxe")
AUTH0_BFF_CLIENT_SECRET = os.environ.get("AUTH0_BFF_CLIENT_SECRET", "")
AUTH0_API_AUDIENCE = os.environ.get("AUTH0_API_AUDIENCE", "https://api.rag-health.example.com")
AUTH0_MYACCOUNT_AUDIENCE = os.environ.get("AUTH0_MYACCOUNT_AUDIENCE", "")
AUTH0_CALLBACK_URL = os.environ.get("AUTH0_CALLBACK_URL", "")
API_DOMAIN = os.environ.get("API_DOMAIN", "")
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "https://rag-health.demo-connect.us")
OAUTH_STATE_TABLE_NAME = os.environ.get("OAUTH_STATE_TABLE_NAME", "rag-health-oauth-state-dev")

OAUTH_SCOPES = "openid profile email offline_access read:content read:calendar write:calendar read:me:connected_accounts"

dynamodb = boto3.resource("dynamodb")
state_table = dynamodb.Table(OAUTH_STATE_TABLE_NAME)


class OAuthError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def generate_pkce_pair() -> tuple[str, str]:
    code_verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return code_verifier, code_challenge


def store_oauth_state(state: str, code_verifier: str, redirect_uri: str) -> None:
    state_table.put_item(Item={
        "state": state,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
        "expires_at": int(time.time()) + 600,
    })


def retrieve_oauth_state(state: str) -> Optional[Dict[str, Any]]:
    try:
        response = state_table.get_item(Key={"state": state})
        state_data = response.get("Item")
        if not state_data or state_data.get("expires_at", 0) < int(time.time()):
            return None
        state_table.delete_item(Key={"state": state})
        return state_data
    except ClientError as e:
        print(f"[OAuth] Failed to retrieve state: {e}")
        return None


def handle_login(event: Dict[str, Any]) -> Dict[str, Any]:
    callback_url = AUTH0_CALLBACK_URL
    if not callback_url:
        if API_DOMAIN:
            callback_url = f"https://{API_DOMAIN}/auth/callback"
        else:
            host = event.get("requestContext", {}).get("domainName", "")
            callback_url = f"https://{host}/auth/callback"

    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    store_oauth_state(state, code_verifier, callback_url)

    auth_params = {
        "response_type": "code",
        "client_id": AUTH0_BFF_CLIENT_ID,
        "redirect_uri": callback_url,
        "scope": OAUTH_SCOPES,
        "audience": AUTH0_API_AUDIENCE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    authorization_url = f"https://{AUTH0_DOMAIN}/authorize?{urlencode(auth_params)}"
    return {"statusCode": 200, "body": json.dumps({"authorization_url": authorization_url})}


def handle_callback(event: Dict[str, Any]) -> Dict[str, Any]:
    query_params = event.get("queryStringParameters", {}) or {}
    code = query_params.get("code")
    state = query_params.get("state")
    error = query_params.get("error")

    if error:
        return _redirect_with_error(f"Authorization failed: {query_params.get('error_description', error)}")
    if not code or not state:
        return _redirect_with_error("Missing code or state parameter")

    state_data = retrieve_oauth_state(state)
    if not state_data:
        return _redirect_with_error("Invalid or expired state")

    try:
        tokens = exchange_code_for_tokens(code, state_data["code_verifier"], state_data["redirect_uri"])
    except OAuthError as e:
        return _redirect_with_error(e.message)

    try:
        user_info = get_user_info(tokens.get("access_token"))
    except OAuthError as e:
        return _redirect_with_error(e.message)

    roles = user_info.get("https://rag-health.example.com/roles", [])

    refresh_token = tokens.get("refresh_token")
    myaccount_token = None
    if refresh_token:
        myaccount_token = get_myaccount_token(refresh_token)

    # Check if Google is already connected in Token Vault
    google_connected = False
    if myaccount_token:
        google_connected = check_google_connected(myaccount_token)

    # Subscription tier is managed in FGA
    from fga_retriever import get_user_subscription_from_fga
    fga_user_id = user_info.get("sub", "")
    subscription_tier = get_user_subscription_from_fga(fga_user_id)

    session_id = create_session(
        user_id=user_info.get("sub"),
        email=user_info.get("email", ""),
        name=user_info.get("name"),
        picture=user_info.get("picture"),
        subscription_tier=subscription_tier,
        roles=roles if isinstance(roles, list) else [],
        access_token=tokens.get("access_token", ""),
        refresh_token=refresh_token,
        id_token=tokens.get("id_token"),
        google_connected=google_connected,
    )

    return {
        "statusCode": 302,
        "headers": {
            "Location": FRONTEND_ORIGIN,
            "Set-Cookie": build_session_cookie(session_id),
            "Cache-Control": "no-store",
        },
        "body": "",
    }


def exchange_code_for_tokens(code: str, code_verifier: str, redirect_uri: str) -> Dict[str, Any]:
    token_url = f"https://{AUTH0_DOMAIN}/oauth/token"
    payload = {
        "grant_type": "authorization_code",
        "client_id": AUTH0_BFF_CLIENT_ID,
        "client_secret": AUTH0_BFF_CLIENT_SECRET,
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }
    try:
        response = requests.post(
            token_url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        if response.status_code != 200:
            error_data = response.json()
            raise OAuthError(error_data.get("error_description", "Token exchange failed"))
        return response.json()
    except requests.RequestException as e:
        raise OAuthError("Failed to exchange authorization code")


def get_user_info(access_token: str) -> Dict[str, Any]:
    try:
        response = requests.get(
            f"https://{AUTH0_DOMAIN}/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if response.status_code != 200:
            raise OAuthError("Failed to get user info")
        return response.json()
    except requests.RequestException:
        raise OAuthError("Failed to get user info")


def get_myaccount_token(refresh_token: str) -> Optional[str]:
    """Exchange refresh token for a MyAccount API access token."""
    if not refresh_token or not AUTH0_MYACCOUNT_AUDIENCE:
        return None
    try:
        response = requests.post(
            f"https://{AUTH0_DOMAIN}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": AUTH0_BFF_CLIENT_ID,
                "client_secret": AUTH0_BFF_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "audience": AUTH0_MYACCOUNT_AUDIENCE,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        if response.status_code != 200:
            print(f"[OAuth] MyAccount token exchange failed: {response.status_code} - {response.json()}")
            return None
        return response.json().get("access_token")
    except requests.RequestException as e:
        print(f"[OAuth] MyAccount token request failed: {e}")
        return None


def check_google_connected(myaccount_token: str) -> bool:
    """Check if user has Google connected in Auth0 Token Vault."""
    try:
        response = requests.get(
            f"https://{AUTH0_DOMAIN}/me/v1/connected-accounts/accounts",
            params={"connection": "google-oauth2"},
            headers={"Authorization": f"Bearer {myaccount_token}"},
            timeout=5,
        )
        if response.status_code == 200:
            data = response.json()
            connected = len(data.get("accounts", [])) > 0
            print(f"[OAuth] Token Vault Google connected: {connected}")
            return connected
    except Exception as e:
        print(f"[OAuth] Token Vault check failed: {e}")
    return False


def handle_connect_google(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle POST /auth/connect/google - Initiate Auth0 Connected Accounts flow.

    Uses the Token Vault API to register a Google connected account so that
    the connection is stored in Auth0 and visible in the dashboard.
    """
    headers = event.get("headers", {})
    cookie_header = headers.get("cookie") or headers.get("Cookie")
    session_id = extract_session_id_from_cookie(cookie_header)

    if not session_id:
        return {"statusCode": 401, "body": json.dumps({"error": "Not authenticated"})}

    session = validate_session(session_id)
    if not session:
        return {"statusCode": 401, "body": json.dumps({"error": "Invalid session"})}

    refresh_token = session.get("refresh_token")
    if not refresh_token:
        return {"statusCode": 400, "body": json.dumps({"error": "No refresh token in session"})}

    # Get a fresh MyAccount access token
    myaccount_token = get_myaccount_token(refresh_token)
    if not myaccount_token:
        return {"statusCode": 500, "body": json.dumps({"error": "Failed to get MyAccount token"})}

    callback_url = (
        f"https://{API_DOMAIN}/auth/connect/callback"
        if API_DOMAIN
        else AUTH0_CALLBACK_URL.replace("/auth/callback", "/auth/connect/callback")
    )

    state = secrets.token_urlsafe(16)

    # Call the Connected Accounts API to get an authorization URL
    try:
        response = requests.post(
            f"https://{AUTH0_DOMAIN}/me/v1/connected-accounts/connect",
            headers={
                "Authorization": f"Bearer {myaccount_token}",
                "Content-Type": "application/json",
            },
            json={
                "connection": "google-oauth2",
                "redirect_uri": callback_url,
                "state": state,
                "scopes": [
                    "openid",
                    "profile",
                    "email",
                    "https://www.googleapis.com/auth/calendar",
                ],
            },
            timeout=10,
        )
        if response.status_code not in (200, 201):
            err = response.json()
            print(f"[OAuth] Connected Accounts initiation failed: {response.status_code} - {err}")
            return {"statusCode": 500, "body": json.dumps({"error": f"Auth0 error: {err.get('message', 'Unknown error')}"})}
        data = response.json()
    except requests.RequestException as e:
        print(f"[OAuth] Connected Accounts request failed: {e}")
        return {"statusCode": 500, "body": json.dumps({"error": "Failed to initiate Google connection"})}

    auth_session = data.get("auth_session")
    connect_uri = data.get("connect_uri")
    connect_params = data.get("connect_params", {})
    ticket = connect_params.get("ticket")

    if not auth_session or not connect_uri or not ticket:
        print(f"[OAuth] Unexpected Connected Accounts response: {data}")
        return {"statusCode": 500, "body": json.dumps({"error": "Unexpected response from Auth0 Token Vault"})}

    # Store auth_session keyed by state for the callback
    state_table.put_item(Item={
        "state": state,
        "auth_session": auth_session,
        "session_id": session_id,
        "flow_type": "connected_accounts",
        "expires_at": int(time.time()) + 600,
    })

    authorization_url = f"{connect_uri}?ticket={ticket}"
    print(f"[OAuth] Connected Accounts initiated — ticket: {ticket[:8]}...")

    return {"statusCode": 200, "body": json.dumps({"authorization_url": authorization_url})}


def handle_connect_callback(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle GET /auth/connect/callback - Complete Connected Accounts flow.

    Auth0 redirects here with connect_code + state after the user authorizes Google.
    We call /me/v1/connected-accounts/complete to register the connection in Token Vault.
    """
    query_params = event.get("queryStringParameters", {}) or {}
    connect_code = query_params.get("connect_code")
    state = query_params.get("state")
    error = query_params.get("error")

    if error:
        return _redirect_with_error(f"Connection failed: {query_params.get('error_description', error)}")
    if not connect_code:
        return _redirect_with_error("Missing connect_code in callback")

    # Look up stored auth_session by state
    try:
        state_response = state_table.get_item(Key={"state": state})
        state_data = state_response.get("Item")
        if not state_data or state_data.get("flow_type") != "connected_accounts":
            return _redirect_with_error("Invalid or expired state")
        if state_data.get("expires_at", 0) < int(time.time()):
            return _redirect_with_error("Connection request expired")

        auth_session = state_data["auth_session"]
        session_id = state_data["session_id"]
        state_table.delete_item(Key={"state": state})
    except ClientError as e:
        print(f"[OAuth] State lookup error: {e}")
        return _redirect_with_error("State validation failed")

    session = validate_session(session_id)
    if not session:
        return _redirect_with_error("Session expired. Please log in again.")

    # Get a fresh MyAccount token for the complete call
    refresh_token = session.get("refresh_token")
    myaccount_token = get_myaccount_token(refresh_token)
    if not myaccount_token:
        return _redirect_with_error("Failed to authenticate for connection completion")

    callback_url = (
        f"https://{API_DOMAIN}/auth/connect/callback"
        if API_DOMAIN
        else AUTH0_CALLBACK_URL.replace("/auth/callback", "/auth/connect/callback")
    )

    # Complete the Connected Accounts flow — Auth0 stores tokens in Token Vault
    try:
        response = requests.post(
            f"https://{AUTH0_DOMAIN}/me/v1/connected-accounts/complete",
            headers={
                "Authorization": f"Bearer {myaccount_token}",
                "Content-Type": "application/json",
            },
            json={
                "auth_session": auth_session,
                "connect_code": connect_code,
                "redirect_uri": callback_url,
            },
            timeout=10,
        )
        if response.status_code not in (200, 201):
            err = response.json()
            print(f"[OAuth] Connected Accounts complete failed: {response.status_code} - {err}")
            return _redirect_with_error(f"Failed to complete Google connection: {err.get('message', '')}")

        result = response.json()
        print(f"[OAuth] Connected Account registered: connection={result.get('connection')}, id={result.get('id')}")
    except requests.RequestException as e:
        print(f"[OAuth] Connected Accounts complete request failed: {e}")
        return _redirect_with_error("Failed to complete Google connection")

    # Mark session as Google connected
    update_session_google_connected(session_id, connected=True)

    return {
        "statusCode": 302,
        "headers": {
            "Location": f"{FRONTEND_ORIGIN}?connected=google",
            "Cache-Control": "no-store",
        },
        "body": "",
    }


def get_google_token_from_connected_accounts(refresh_token: str) -> Optional[str]:
    """
    Retrieve a Google access token from Auth0 Token Vault.

    Uses the Federated Connection Token Exchange grant. Auth0 looks up the stored
    Google token in Token Vault and returns a fresh access token.

    Args:
        refresh_token: The user's main Auth0 refresh token (from session).
    """
    return get_google_token_via_token_exchange(refresh_token, connection="google-oauth2")


def get_google_token_via_token_exchange(refresh_token: str, connection: str = "google-oauth2") -> Optional[str]:
    """Exchange Auth0 refresh token for an IdP access token via Federated Token Exchange."""
    if not refresh_token:
        return None

    try:
        response = requests.post(
            f"https://{AUTH0_DOMAIN}/oauth/token",
            data={
                "grant_type": "urn:auth0:params:oauth:grant-type:token-exchange:federated-connection-access-token",
                "client_id": AUTH0_BFF_CLIENT_ID,
                "client_secret": AUTH0_BFF_CLIENT_SECRET,
                "subject_token": refresh_token,
                "subject_token_type": "urn:ietf:params:oauth:token-type:refresh_token",
                "requested_token_type": "http://auth0.com/oauth/token-type/federated-connection-access-token",
                "connection": connection,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )
        if response.status_code != 200:
            print(f"[OAuth] Federated token exchange failed: {response.status_code} - {response.json()}")
            return None
        return response.json().get("access_token")
    except requests.RequestException as e:
        print(f"[OAuth] Federated token exchange request failed: {e}")
        return None


def handle_logout(event: Dict[str, Any]) -> Dict[str, Any]:
    headers = event.get("headers", {})
    cookie_header = headers.get("cookie") or headers.get("Cookie")
    session_id = extract_session_id_from_cookie(cookie_header)
    if session_id:
        delete_session(session_id)
    return {
        "statusCode": 200,
        "headers": {"Set-Cookie": build_clear_session_cookie()},
        "body": json.dumps({"success": True}),
    }


def handle_me(event: Dict[str, Any]) -> Dict[str, Any]:
    headers = event.get("headers", {})
    cookie_header = headers.get("cookie") or headers.get("Cookie")
    session_id = extract_session_id_from_cookie(cookie_header)

    if not session_id:
        return {"statusCode": 401, "body": json.dumps({"authenticated": False, "error": "No session"})}

    session = validate_session(session_id)
    if not session:
        return {
            "statusCode": 401,
            "headers": {"Set-Cookie": build_clear_session_cookie()},
            "body": json.dumps({"authenticated": False, "error": "Invalid session"}),
        }

    user_context = get_user_context(session)
    google_connected = bool(session.get("google_connected"))

    return {
        "statusCode": 200,
        "body": json.dumps({
            "authenticated": True,
            "user": {
                "id": user_context["user_id"],
                "email": user_context["email"],
                "name": user_context["name"],
                "picture": user_context["picture"],
                "subscription_tier": user_context["subscription_tier"],
                "roles": user_context["roles"],
            },
            "googleConnected": google_connected,
        }),
    }


def _redirect_with_error(error_message: str) -> Dict[str, Any]:
    return {
        "statusCode": 302,
        "headers": {
            "Location": f"{FRONTEND_ORIGIN}?auth_error={quote(error_message)}",
            "Cache-Control": "no-store",
        },
        "body": "",
    }
