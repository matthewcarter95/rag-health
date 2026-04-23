"""
Auth0 Token Vault Integration

Retrieves Google tokens from Auth0 Token Vault using M2M credentials.
This replaces the MyAccount token approach where the frontend had to
pass tokens to the backend.
"""

import os
import time
from typing import Optional, Dict, Any

import requests
from cachetools import TTLCache

# Configuration
AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "violet-hookworm-18506.cic-demo-platform.auth0app.com")
AUTH0_M2M_CLIENT_ID = os.environ.get("AUTH0_M2M_CLIENT_ID", "")
AUTH0_M2M_CLIENT_SECRET = os.environ.get("AUTH0_M2M_CLIENT_SECRET", "")

# Cache for M2M access token (5 minute TTL)
_m2m_token_cache: TTLCache = TTLCache(maxsize=1, ttl=300)


class TokenVaultError(Exception):
    """Token Vault operation error."""

    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def get_m2m_access_token() -> str:
    """
    Get M2M access token for Auth0 Management API.

    Uses cached token if available.

    Returns:
        M2M access token

    Raises:
        TokenVaultError: If token request fails
    """
    # Check cache
    cached_token = _m2m_token_cache.get("token")
    if cached_token:
        return cached_token

    if not AUTH0_M2M_CLIENT_ID or not AUTH0_M2M_CLIENT_SECRET:
        raise TokenVaultError("M2M credentials not configured", 500)

    token_url = f"https://{AUTH0_DOMAIN}/oauth/token"

    payload = {
        "grant_type": "client_credentials",
        "client_id": AUTH0_M2M_CLIENT_ID,
        "client_secret": AUTH0_M2M_CLIENT_SECRET,
        "audience": f"https://{AUTH0_DOMAIN}/api/v2/",
    }

    try:
        response = requests.post(
            token_url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )

        if response.status_code != 200:
            print(f"[TokenVault] M2M token request failed: {response.status_code}")
            raise TokenVaultError("Failed to get M2M token")

        token_data = response.json()
        access_token = token_data.get("access_token")

        if not access_token:
            raise TokenVaultError("No access token in M2M response")

        # Cache the token
        _m2m_token_cache["token"] = access_token

        return access_token

    except requests.RequestException as e:
        print(f"[TokenVault] M2M token request error: {e}")
        raise TokenVaultError("Failed to get M2M token")


def get_google_token_from_vault(user_id: str) -> Optional[str]:
    """
    Get Google access token from Auth0 Token Vault.

    Uses Auth0 Management API to retrieve the user's Google identity
    provider token stored in the Token Vault.

    Args:
        user_id: Auth0 user ID (e.g., "google-oauth2|123456")

    Returns:
        Google access token if available, None otherwise

    Raises:
        TokenVaultError: If API call fails
    """
    if not user_id:
        print("[TokenVault] No user_id provided")
        return None

    try:
        m2m_token = get_m2m_access_token()
    except TokenVaultError:
        return None

    # Auth0 Management API endpoint for user identities
    # The Token Vault stores provider tokens in the user's identities
    users_url = f"https://{AUTH0_DOMAIN}/api/v2/users/{user_id}"

    try:
        response = requests.get(
            users_url,
            headers={
                "Authorization": f"Bearer {m2m_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )

        print(f"[TokenVault] User lookup response: {response.status_code}")

        if response.status_code == 404:
            print(f"[TokenVault] User not found: {user_id}")
            return None

        if response.status_code != 200:
            print(f"[TokenVault] User lookup failed: {response.status_code} - {response.text}")
            return None

        user_data = response.json()

        # Find Google identity in identities array
        identities = user_data.get("identities", [])
        for identity in identities:
            if identity.get("provider") == "google-oauth2":
                access_token = identity.get("access_token")
                if access_token:
                    print("[TokenVault] Found Google access token in identity")
                    return access_token

        print("[TokenVault] No Google access token found in user identities")
        return None

    except requests.RequestException as e:
        print(f"[TokenVault] User lookup error: {e}")
        return None


def get_google_token_from_federated_connections(user_id: str) -> Optional[str]:
    """
    Alternative: Get Google token from Federated Connections Token endpoint.

    This uses the newer Token Vault API if available on the tenant.

    Args:
        user_id: Auth0 user ID

    Returns:
        Google access token if available, None otherwise
    """
    if not user_id:
        return None

    try:
        m2m_token = get_m2m_access_token()
    except TokenVaultError:
        return None

    # Extract the Google user ID from the Auth0 user_id
    # Format: "google-oauth2|{google_id}"
    if not user_id.startswith("google-oauth2|"):
        print(f"[TokenVault] User {user_id} is not a Google user")
        return None

    google_user_id = user_id.split("|")[1]

    # Federated Connections Token Vault endpoint
    token_url = f"https://{AUTH0_DOMAIN}/api/v2/users/{user_id}/federated-connections-tokens"

    try:
        response = requests.get(
            token_url,
            headers={
                "Authorization": f"Bearer {m2m_token}",
                "Content-Type": "application/json",
            },
            params={
                "connection": "google-oauth2",
            },
            timeout=10,
        )

        print(f"[TokenVault] Federated token response: {response.status_code}")

        if response.status_code == 200:
            token_data = response.json()
            # Response format: [{"connection": "google-oauth2", "access_token": "..."}]
            if isinstance(token_data, list) and len(token_data) > 0:
                return token_data[0].get("access_token")

        # Fall back to standard identity lookup
        return None

    except requests.RequestException as e:
        print(f"[TokenVault] Federated token error: {e}")
        return None


def get_google_token(user_id: str, session: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """
    Get Google access token for calendar operations.

    Tries multiple methods:
    1. Federated Connections Token Vault (if available)
    2. User identities lookup (Management API)

    Args:
        user_id: Auth0 user ID
        session: Optional session data (not used currently, but kept for interface compatibility)

    Returns:
        Google access token if available, None otherwise
    """
    # Try Federated Connections first (newer Token Vault API)
    token = get_google_token_from_federated_connections(user_id)
    if token:
        return token

    # Fall back to identity lookup
    token = get_google_token_from_vault(user_id)
    if token:
        return token

    print(f"[TokenVault] Could not retrieve Google token for user {user_id}")
    return None
