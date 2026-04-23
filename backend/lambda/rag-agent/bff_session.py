"""
BFF Session Management Module

Manages HTTP-only session cookies with DynamoDB persistence.
Sessions store user context and Auth0 tokens for the BFF pattern.
"""

import os
import secrets
import time
from typing import Optional, Dict, Any

import boto3
from botocore.exceptions import ClientError

# Configuration
SESSION_TABLE_NAME = os.environ.get("SESSION_TABLE_NAME", "rag-health-sessions-dev")
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE", "86400"))  # 24 hours
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")

# DynamoDB client
dynamodb = boto3.resource("dynamodb")
sessions_table = dynamodb.Table(SESSION_TABLE_NAME)


class SessionError(Exception):
    """Session operation error."""

    def __init__(self, message: str, status_code: int = 401):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def generate_session_id() -> str:
    """Generate a cryptographically secure session ID (64-char hex)."""
    return secrets.token_hex(32)


def create_session(
    user_id: str,
    email: str,
    name: Optional[str],
    picture: Optional[str],
    subscription_tier: str,
    roles: list,
    access_token: str,
    refresh_token: Optional[str] = None,
    id_token: Optional[str] = None,
) -> str:
    """
    Create a new session in DynamoDB.

    Args:
        user_id: Auth0 user ID (sub claim)
        email: User email
        name: User display name
        picture: Profile picture URL
        subscription_tier: User's subscription tier
        roles: List of user roles
        access_token: Auth0 access token
        refresh_token: Auth0 refresh token (optional)
        id_token: Auth0 ID token (optional)

    Returns:
        session_id: The generated session ID

    Raises:
        SessionError: If session creation fails
    """
    session_id = generate_session_id()
    now = int(time.time())
    expires_at = now + SESSION_MAX_AGE

    session_data = {
        "session_id": session_id,
        "user_id": user_id,
        "email": email,
        "name": name or "",
        "picture": picture or "",
        "subscription_tier": subscription_tier,
        "roles": roles,
        "access_token": access_token,
        "refresh_token": refresh_token or "",
        "id_token": id_token or "",
        "created_at": now,
        "expires_at": expires_at,
    }

    try:
        sessions_table.put_item(Item=session_data)
        print(f"[Session] Created session for user {user_id}")
        return session_id
    except ClientError as e:
        print(f"[Session] Failed to create session: {e}")
        raise SessionError("Failed to create session", 500)


def validate_session(session_id: str) -> Optional[Dict[str, Any]]:
    """
    Validate a session ID and return session data.

    Args:
        session_id: The session ID from cookie

    Returns:
        Session data dict if valid, None if invalid/expired
    """
    if not session_id:
        return None

    try:
        response = sessions_table.get_item(Key={"session_id": session_id})
        session = response.get("Item")

        if not session:
            print(f"[Session] Session not found: {session_id[:8]}...")
            return None

        # Check expiration
        now = int(time.time())
        if session.get("expires_at", 0) < now:
            print(f"[Session] Session expired: {session_id[:8]}...")
            # Clean up expired session
            delete_session(session_id)
            return None

        return session

    except ClientError as e:
        print(f"[Session] Failed to validate session: {e}")
        return None


def delete_session(session_id: str) -> bool:
    """
    Delete a session from DynamoDB.

    Args:
        session_id: The session ID to delete

    Returns:
        True if deleted, False if failed
    """
    if not session_id:
        return False

    try:
        sessions_table.delete_item(Key={"session_id": session_id})
        print(f"[Session] Deleted session: {session_id[:8]}...")
        return True
    except ClientError as e:
        print(f"[Session] Failed to delete session: {e}")
        return False


def update_session_tokens(
    session_id: str,
    access_token: str,
    refresh_token: Optional[str] = None,
) -> bool:
    """
    Update tokens in an existing session.

    Args:
        session_id: The session ID
        access_token: New access token
        refresh_token: New refresh token (optional)

    Returns:
        True if updated, False if failed
    """
    if not session_id:
        return False

    try:
        update_expr = "SET access_token = :at"
        expr_values = {":at": access_token}

        if refresh_token:
            update_expr += ", refresh_token = :rt"
            expr_values[":rt"] = refresh_token

        sessions_table.update_item(
            Key={"session_id": session_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
        )
        print(f"[Session] Updated tokens for session: {session_id[:8]}...")
        return True
    except ClientError as e:
        print(f"[Session] Failed to update tokens: {e}")
        return False


def get_user_context(session: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract user context from session for use in handlers.

    Args:
        session: Session data from validate_session

    Returns:
        User context dict compatible with existing handler code
    """
    return {
        "user_id": session.get("user_id"),
        "email": session.get("email"),
        "name": session.get("name"),
        "picture": session.get("picture"),
        "subscription_tier": session.get("subscription_tier", "basic"),
        "roles": session.get("roles", []),
    }


def extract_session_id_from_cookie(cookie_header: Optional[str]) -> Optional[str]:
    """
    Extract session_id from Cookie header.

    Args:
        cookie_header: The Cookie header value

    Returns:
        session_id if found, None otherwise
    """
    if not cookie_header:
        return None

    # Parse cookies (format: "key1=value1; key2=value2")
    cookies = {}
    for part in cookie_header.split(";"):
        part = part.strip()
        if "=" in part:
            key, value = part.split("=", 1)
            cookies[key.strip()] = value.strip()

    return cookies.get("session_id")


def build_session_cookie(session_id: str, max_age: int = SESSION_MAX_AGE) -> str:
    """
    Build Set-Cookie header value for session cookie.

    Cross-origin cookies require SameSite=None and Secure.

    Args:
        session_id: The session ID
        max_age: Cookie max age in seconds

    Returns:
        Set-Cookie header value
    """
    return (
        f"session_id={session_id}; "
        f"HttpOnly; "
        f"Secure; "
        f"SameSite=None; "
        f"Path=/; "
        f"Max-Age={max_age}"
    )


def build_clear_session_cookie() -> str:
    """
    Build Set-Cookie header to clear the session cookie.

    Returns:
        Set-Cookie header value that expires the cookie
    """
    return (
        "session_id=; "
        "HttpOnly; "
        "Secure; "
        "SameSite=None; "
        "Path=/; "
        "Max-Age=0"
    )
