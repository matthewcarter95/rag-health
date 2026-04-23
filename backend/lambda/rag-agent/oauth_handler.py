"""
OAuth Authorization Code Flow Handler

Implements BFF OAuth flow using Auth0 as the identity provider.
The backend acts as a confidential client, exchanging authorization
codes for tokens and managing sessions.
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
)

# Configuration
AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "violet-hookworm-18506.cic-demo-platform.auth0app.com")
AUTH0_BFF_CLIENT_ID = os.environ.get("AUTH0_BFF_CLIENT_ID", "gYVmHq3MbrI73Uf1Qikb1ze5KfBdDLxe")
AUTH0_BFF_CLIENT_SECRET = os.environ.get("AUTH0_BFF_CLIENT_SECRET", "")
AUTH0_API_AUDIENCE = os.environ.get("AUTH0_API_AUDIENCE", "https://api.rag-health.example.com")
AUTH0_MYACCOUNT_AUDIENCE = os.environ.get("AUTH0_MYACCOUNT_AUDIENCE", "")
AUTH0_CALLBACK_URL = os.environ.get("AUTH0_CALLBACK_URL", "")
API_DOMAIN = os.environ.get("API_DOMAIN", "")  # Custom domain (e.g., api.rag-health.demo-connect.us)
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "https://rag-health.demo-connect.us")
OAUTH_STATE_TABLE_NAME = os.environ.get("OAUTH_STATE_TABLE_NAME", "rag-health-oauth-state-dev")

# OAuth scopes to request (includes MyAccount scopes for Connected Accounts access)
OAUTH_SCOPES = "openid profile email offline_access read:content read:calendar write:calendar read:me:connected_accounts"

# DynamoDB for OAuth state
dynamodb = boto3.resource("dynamodb")
state_table = dynamodb.Table(OAUTH_STATE_TABLE_NAME)


class OAuthError(Exception):
    """OAuth operation error."""

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def generate_pkce_pair() -> tuple[str, str]:
    """
    Generate PKCE code_verifier and code_challenge.

    Returns:
        (code_verifier, code_challenge) tuple
    """
    # Generate random verifier (43-128 chars, URL-safe)
    code_verifier = secrets.token_urlsafe(32)

    # Create challenge using SHA256
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    return code_verifier, code_challenge


def store_oauth_state(state: str, code_verifier: str, redirect_uri: str) -> None:
    """
    Store OAuth state and PKCE verifier in DynamoDB.

    Args:
        state: Random state parameter
        code_verifier: PKCE code verifier
        redirect_uri: Callback redirect URI
    """
    expires_at = int(time.time()) + 600  # 10 minute TTL

    state_table.put_item(
        Item={
            "state": state,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "expires_at": expires_at,
        }
    )


def retrieve_oauth_state(state: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve and delete OAuth state from DynamoDB.

    Args:
        state: The state parameter from callback

    Returns:
        State data if valid, None if not found/expired
    """
    try:
        response = state_table.get_item(Key={"state": state})
        state_data = response.get("Item")

        if not state_data:
            return None

        # Check expiration
        if state_data.get("expires_at", 0) < int(time.time()):
            return None

        # Delete after retrieval (one-time use)
        state_table.delete_item(Key={"state": state})

        return state_data

    except ClientError as e:
        print(f"[OAuth] Failed to retrieve state: {e}")
        return None


def handle_login(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle POST /auth/login - Initiate OAuth flow.

    Returns authorization URL for frontend to redirect to.
    """
    # Determine callback URL
    callback_url = AUTH0_CALLBACK_URL
    if not callback_url:
        # Prefer custom API domain if configured, otherwise use Lambda Function URL
        if API_DOMAIN:
            callback_url = f"https://{API_DOMAIN}/auth/callback"
        else:
            host = event.get("requestContext", {}).get("domainName", "")
            callback_url = f"https://{host}/auth/callback"

    # Generate PKCE pair and state
    code_verifier, code_challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)

    # Store state for callback verification
    store_oauth_state(state, code_verifier, callback_url)
    print(f"[OAuth] Login initiated - callback_url: {callback_url}, state: {state[:8]}...")

    # Build Auth0 authorization URL
    auth_params = {
        "response_type": "code",
        "client_id": AUTH0_BFF_CLIENT_ID,
        "redirect_uri": callback_url,
        "scope": OAUTH_SCOPES,
        "audience": AUTH0_API_AUDIENCE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # Request Google login for calendar access
        "connection": "google-oauth2",
    }

    authorization_url = f"https://{AUTH0_DOMAIN}/authorize?{urlencode(auth_params)}"

    return {
        "statusCode": 200,
        "body": json.dumps({
            "authorization_url": authorization_url,
        }),
    }


def handle_callback(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle GET /auth/callback - OAuth callback from Auth0.

    Exchanges authorization code for tokens and creates session.
    """
    # Extract query parameters
    query_params = event.get("queryStringParameters", {}) or {}
    code = query_params.get("code")
    state = query_params.get("state")
    error = query_params.get("error")
    error_description = query_params.get("error_description", "")

    # Check for OAuth errors
    if error:
        print(f"[OAuth] Authorization error: {error} - {error_description}")
        return _redirect_with_error(f"Authorization failed: {error_description}")

    if not code or not state:
        return _redirect_with_error("Missing code or state parameter")

    # Retrieve and validate state
    state_data = retrieve_oauth_state(state)
    if not state_data:
        return _redirect_with_error("Invalid or expired state")

    code_verifier = state_data.get("code_verifier")
    redirect_uri = state_data.get("redirect_uri")

    # Exchange code for tokens
    try:
        tokens = exchange_code_for_tokens(code, code_verifier, redirect_uri)
    except OAuthError as e:
        return _redirect_with_error(e.message)

    # Extract user info from ID token or userinfo endpoint
    try:
        user_info = get_user_info(tokens.get("access_token"))
    except OAuthError as e:
        return _redirect_with_error(e.message)

    # Extract subscription tier and roles from token claims
    subscription_tier = user_info.get("https://rag-health.example.com/subscription_tier", "basic")
    roles = user_info.get("https://rag-health.example.com/roles", [])

    # Get MyAccount token for Connected Accounts API (calendar access)
    myaccount_token = None
    refresh_token = tokens.get("refresh_token")
    if refresh_token:
        myaccount_token = get_myaccount_token(refresh_token)

    # Create session
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
        myaccount_token=myaccount_token,
    )

    # Redirect to frontend with session cookie
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
    """
    Exchange authorization code for tokens.

    Args:
        code: Authorization code from Auth0
        code_verifier: PKCE code verifier
        redirect_uri: The callback URI used

    Returns:
        Token response dict

    Raises:
        OAuthError: If token exchange fails
    """
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
            error_msg = error_data.get("error_description", error_data.get("error", "Token exchange failed"))
            print(f"[OAuth] Token exchange failed: {response.status_code} - {error_msg}")
            print(f"[OAuth] Full error response: {error_data}")
            print(f"[OAuth] Request payload (sans secret): grant_type={payload['grant_type']}, client_id={payload['client_id']}, redirect_uri={payload['redirect_uri']}, code_verifier_len={len(payload.get('code_verifier', ''))}")
            raise OAuthError(error_msg)

        return response.json()

    except requests.RequestException as e:
        print(f"[OAuth] Token exchange request failed: {e}")
        raise OAuthError("Failed to exchange authorization code")


def get_user_info(access_token: str) -> Dict[str, Any]:
    """
    Get user info from Auth0 userinfo endpoint.

    Args:
        access_token: Auth0 access token

    Returns:
        User info dict

    Raises:
        OAuthError: If userinfo request fails
    """
    userinfo_url = f"https://{AUTH0_DOMAIN}/userinfo"

    try:
        response = requests.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )

        if response.status_code != 200:
            print(f"[OAuth] Userinfo failed: {response.status_code}")
            raise OAuthError("Failed to get user info")

        return response.json()

    except requests.RequestException as e:
        print(f"[OAuth] Userinfo request failed: {e}")
        raise OAuthError("Failed to get user info")


def get_myaccount_token(refresh_token: str) -> Optional[str]:
    """
    Exchange refresh token for a MyAccount access token.

    The MyAccount token is needed to access Auth0 Connected Accounts API
    for retrieving Google Calendar tokens.

    Args:
        refresh_token: Auth0 refresh token from initial auth

    Returns:
        MyAccount access token if successful, None otherwise
    """
    if not refresh_token or not AUTH0_MYACCOUNT_AUDIENCE:
        print("[OAuth] Missing refresh_token or MyAccount audience config")
        return None

    token_url = f"https://{AUTH0_DOMAIN}/oauth/token"

    payload = {
        "grant_type": "refresh_token",
        "client_id": AUTH0_BFF_CLIENT_ID,
        "client_secret": AUTH0_BFF_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "audience": AUTH0_MYACCOUNT_AUDIENCE,
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
            print(f"[OAuth] MyAccount token exchange failed: {response.status_code} - {error_data}")
            return None

        token_data = response.json()
        myaccount_token = token_data.get("access_token")
        print(f"[OAuth] Successfully obtained MyAccount token")
        return myaccount_token

    except requests.RequestException as e:
        print(f"[OAuth] MyAccount token request failed: {e}")
        return None


def get_google_token_via_token_exchange(subject_token: str, user_id: str) -> Optional[str]:
    """
    Exchange Auth0 access token for Google access token using Federated Connection Token Exchange.

    Uses the grant type: urn:auth0:params:oauth:grant-type:token-exchange:federated-connection-access-token

    Args:
        subject_token: Auth0 access token
        user_id: Auth0 user ID (e.g., "google-oauth2|123456")

    Returns:
        Google access token if successful, None otherwise
    """
    if not subject_token or not user_id:
        print("[OAuth] Missing subject_token or user_id for token exchange")
        return None

    # Only works for Google users
    if not user_id.startswith("google-oauth2|"):
        print(f"[OAuth] User {user_id} is not a Google user, cannot exchange for Google token")
        return None

    token_url = f"https://{AUTH0_DOMAIN}/oauth/token"

    payload = {
        "grant_type": "urn:auth0:params:oauth:grant-type:token-exchange:federated-connection-access-token",
        "client_id": AUTH0_BFF_CLIENT_ID,
        "client_secret": AUTH0_BFF_CLIENT_SECRET,
        "subject_token": subject_token,
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
        "connection": "google-oauth2",
        "scope": "https://www.googleapis.com/auth/calendar",
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
            print(f"[OAuth] Federated token exchange failed: {response.status_code} - {error_data}")
            return None

        token_data = response.json()
        google_token = token_data.get("access_token")
        print(f"[OAuth] Successfully obtained Google token via federated exchange")
        return google_token

    except requests.RequestException as e:
        print(f"[OAuth] Federated token exchange request failed: {e}")
        return None


def handle_logout(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle POST /auth/logout - End session.

    Clears session cookie and deletes session from DynamoDB.
    """
    # Extract session ID from cookie
    headers = event.get("headers", {})
    cookie_header = headers.get("cookie") or headers.get("Cookie")
    session_id = extract_session_id_from_cookie(cookie_header)

    if session_id:
        delete_session(session_id)

    return {
        "statusCode": 200,
        "headers": {
            "Set-Cookie": build_clear_session_cookie(),
        },
        "body": json.dumps({"success": True}),
    }


def handle_me(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle GET /auth/me - Get current session user.

    Returns user profile if session is valid.
    """
    # Extract session ID from cookie
    headers = event.get("headers", {})
    cookie_header = headers.get("cookie") or headers.get("Cookie")
    print(f"[OAuth] /auth/me called - cookie header present: {bool(cookie_header)}, value: {cookie_header[:50] if cookie_header else 'None'}...")
    session_id = extract_session_id_from_cookie(cookie_header)
    print(f"[OAuth] /auth/me - extracted session_id: {session_id[:16] if session_id else 'None'}...")

    if not session_id:
        return {
            "statusCode": 401,
            "body": json.dumps({"authenticated": False, "error": "No session"}),
        }

    # Validate session
    session = validate_session(session_id)
    if not session:
        return {
            "statusCode": 401,
            "headers": {
                "Set-Cookie": build_clear_session_cookie(),
            },
            "body": json.dumps({"authenticated": False, "error": "Invalid session"}),
        }

    # Return user context
    user_context = get_user_context(session)

    # Check if Google is connected (has Connected Accounts refresh token)
    google_connected = bool(session.get("connected_accounts_refresh_token"))

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
    """
    Redirect to frontend with error message.

    Args:
        error_message: Error to display

    Returns:
        Redirect response
    """
    error_url = f"{FRONTEND_ORIGIN}?auth_error={quote(error_message)}"
    return {
        "statusCode": 302,
        "headers": {
            "Location": error_url,
            "Cache-Control": "no-store",
        },
        "body": "",
    }


# Connected Accounts scopes for token retrieval
CONNECTED_ACCOUNTS_SCOPES = "openid profile offline_access create:me:connected_accounts read:me:connected_accounts delete:me:connected_accounts"


def handle_connect_google(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle POST /auth/connect/google - Initiate Connected Accounts authorization flow.

    This starts the OAuth flow specifically for Connected Accounts, which gives us
    a refresh token that can be used to retrieve identity provider tokens (like Google).

    The user must already be logged in (have a valid session).
    """
    # Verify user is logged in
    headers = event.get("headers", {})
    cookie_header = headers.get("cookie") or headers.get("Cookie")
    session_id = extract_session_id_from_cookie(cookie_header)

    if not session_id:
        return {
            "statusCode": 401,
            "body": json.dumps({"error": "Not authenticated"}),
        }

    session = validate_session(session_id)
    if not session:
        return {
            "statusCode": 401,
            "body": json.dumps({"error": "Invalid session"}),
        }

    # Generate PKCE values
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().rstrip("=")

    # Generate state with session reference
    state = secrets.token_urlsafe(16)

    # Store state with code_verifier and session_id for callback
    state_table.put_item(
        Item={
            "state": state,
            "code_verifier": code_verifier,
            "session_id": session_id,
            "flow_type": "connect_google",
            "ttl": int(time.time()) + 600,  # 10 minute expiry
        }
    )

    # Build Connected Accounts authorization URL
    # Using the MyAccount audience and connected accounts scopes
    callback_url = f"https://{API_DOMAIN}/auth/connect/callback" if API_DOMAIN else AUTH0_CALLBACK_URL.replace("/auth/callback", "/auth/connect/callback")

    auth_params = {
        "response_type": "code",
        "client_id": AUTH0_BFF_CLIENT_ID,
        "redirect_uri": callback_url,
        "scope": CONNECTED_ACCOUNTS_SCOPES,
        "audience": AUTH0_MYACCOUNT_AUDIENCE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "connection": "google-oauth2",  # Force Google connection
        "access_type": "offline",  # Request offline access for refresh token
        "prompt": "consent",  # Force consent to get refresh token
    }

    authorization_url = f"https://{AUTH0_DOMAIN}/authorize?{urlencode(auth_params)}"

    print(f"[OAuth] Connect Google initiated - callback_url: {callback_url}, state: {state[:8]}...")

    return {
        "statusCode": 200,
        "body": json.dumps({
            "authorization_url": authorization_url,
        }),
    }


def handle_connect_callback(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle GET /auth/connect/callback - Process Connected Accounts OAuth callback.

    Exchanges the authorization code for a refresh token that can retrieve IdP tokens.
    """
    # Extract query parameters
    query_params = event.get("queryStringParameters", {}) or {}
    code = query_params.get("code")
    state = query_params.get("state")
    error = query_params.get("error")
    error_description = query_params.get("error_description", "")

    if error:
        print(f"[OAuth] Connect callback error: {error} - {error_description}")
        return _redirect_with_error(f"Connection failed: {error_description or error}")

    if not code or not state:
        return _redirect_with_error("Missing authorization code or state")

    # Validate state and get stored data
    try:
        state_response = state_table.get_item(Key={"state": state})
        state_data = state_response.get("Item")

        if not state_data:
            return _redirect_with_error("Invalid or expired state")

        if state_data.get("flow_type") != "connect_google":
            return _redirect_with_error("Invalid flow type")

        code_verifier = state_data.get("code_verifier")
        session_id = state_data.get("session_id")

        # Clean up state
        state_table.delete_item(Key={"state": state})

    except ClientError as e:
        print(f"[OAuth] State lookup error: {e}")
        return _redirect_with_error("State validation failed")

    # Verify session is still valid
    session = validate_session(session_id)
    if not session:
        return _redirect_with_error("Session expired. Please log in again.")

    # Exchange code for tokens (specifically for Connected Accounts refresh token)
    callback_url = f"https://{API_DOMAIN}/auth/connect/callback" if API_DOMAIN else AUTH0_CALLBACK_URL.replace("/auth/callback", "/auth/connect/callback")

    token_url = f"https://{AUTH0_DOMAIN}/oauth/token"
    token_payload = {
        "grant_type": "authorization_code",
        "client_id": AUTH0_BFF_CLIENT_ID,
        "client_secret": AUTH0_BFF_CLIENT_SECRET,
        "code": code,
        "redirect_uri": callback_url,
        "code_verifier": code_verifier,
    }

    try:
        token_response = requests.post(
            token_url,
            data=token_payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )

        if token_response.status_code != 200:
            error_data = token_response.json()
            print(f"[OAuth] Connect token exchange failed: {token_response.status_code} - {error_data}")
            return _redirect_with_error(f"Token exchange failed: {error_data.get('error_description', 'Unknown error')}")

        token_data = token_response.json()
        connected_accounts_refresh_token = token_data.get("refresh_token")

        if not connected_accounts_refresh_token:
            print("[OAuth] No refresh token in connect response")
            return _redirect_with_error("Failed to get Connected Accounts refresh token")

        print("[OAuth] Successfully obtained Connected Accounts refresh token")

        # Update session with the Connected Accounts refresh token
        from bff_session import update_session_connected_accounts
        update_session_connected_accounts(session_id, connected_accounts_refresh_token)

        # Redirect back to frontend with success
        success_url = f"{FRONTEND_ORIGIN}?connected=google"
        return {
            "statusCode": 302,
            "headers": {
                "Location": success_url,
                "Cache-Control": "no-store",
            },
            "body": "",
        }

    except requests.RequestException as e:
        print(f"[OAuth] Connect token request failed: {e}")
        return _redirect_with_error("Token exchange request failed")


def get_google_token_from_connected_accounts(connected_accounts_refresh_token: str) -> Optional[str]:
    """
    Get Google access token using the Connected Accounts refresh token.

    This uses the refresh token obtained from the Connected Accounts flow to:
    1. Get a MyAccount access token with connected accounts scope
    2. Use that token to retrieve the Google access token

    Args:
        connected_accounts_refresh_token: Refresh token from Connected Accounts flow

    Returns:
        Google access token if successful, None otherwise
    """
    if not connected_accounts_refresh_token:
        print("[OAuth] No Connected Accounts refresh token provided")
        return None

    # Step 1: Exchange refresh token for MyAccount access token
    token_url = f"https://{AUTH0_DOMAIN}/oauth/token"

    payload = {
        "grant_type": "refresh_token",
        "client_id": AUTH0_BFF_CLIENT_ID,
        "client_secret": AUTH0_BFF_CLIENT_SECRET,
        "refresh_token": connected_accounts_refresh_token,
        "audience": AUTH0_MYACCOUNT_AUDIENCE,
        "scope": CONNECTED_ACCOUNTS_SCOPES,
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
            print(f"[OAuth] Connected Accounts token refresh failed: {response.status_code} - {error_data}")
            return None

        token_data = response.json()
        myaccount_token = token_data.get("access_token")

        if not myaccount_token:
            print("[OAuth] No access token in refresh response")
            return None

        print("[OAuth] Got MyAccount token from Connected Accounts refresh")

        # Step 2: Use the token to get Google access token via Connected Accounts API
        from google_calendar import get_google_token_via_connected_accounts
        return get_google_token_via_connected_accounts(myaccount_token)

    except requests.RequestException as e:
        print(f"[OAuth] Connected Accounts token refresh request failed: {e}")
        return None
