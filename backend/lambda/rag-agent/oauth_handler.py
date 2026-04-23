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
AUTH0_CALLBACK_URL = os.environ.get("AUTH0_CALLBACK_URL", "")
API_DOMAIN = os.environ.get("API_DOMAIN", "")  # Custom domain (e.g., api.rag-health.demo-connect.us)
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "https://rag-health.demo-connect.us")
OAUTH_STATE_TABLE_NAME = os.environ.get("OAUTH_STATE_TABLE_NAME", "rag-health-oauth-state-dev")

# OAuth scopes to request
OAUTH_SCOPES = "openid profile email offline_access read:content read:calendar write:calendar"

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

    # Create session
    session_id = create_session(
        user_id=user_info.get("sub"),
        email=user_info.get("email", ""),
        name=user_info.get("name"),
        picture=user_info.get("picture"),
        subscription_tier=subscription_tier,
        roles=roles if isinstance(roles, list) else [],
        access_token=tokens.get("access_token", ""),
        refresh_token=tokens.get("refresh_token"),
        id_token=tokens.get("id_token"),
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
