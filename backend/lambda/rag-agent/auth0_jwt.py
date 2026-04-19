"""
Auth0 JWT Validation Module

Validates Auth0 Bearer tokens against JWKS endpoint for Lambda Function URL authentication.
"""

import os
from functools import lru_cache
from typing import Optional

import jwt
from jwt import PyJWKClient
from cachetools import TTLCache

# Configuration from environment
AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "violet-hookworm-18506.cic-demo-platform.auth0app.com")
AUTH0_API_AUDIENCE = os.environ.get("AUTH0_API_AUDIENCE", "https://api.rag-health.example.com")
AUTH0_JWKS_URL = os.environ.get("AUTH0_JWKS_URL", f"https://{AUTH0_DOMAIN}/.well-known/jwks.json")

# Custom claims namespace
CLAIMS_NAMESPACE = "https://rag-health.example.com"

# Cache for JWKS client (reuse across invocations)
_jwks_client: Optional[PyJWKClient] = None

# Token validation cache (5 minute TTL)
_token_cache: TTLCache = TTLCache(maxsize=1000, ttl=300)


class AuthError(Exception):
    """Authentication error with status code and description."""

    def __init__(self, error: str, status_code: int = 401):
        self.error = error
        self.status_code = status_code
        super().__init__(error)


def get_jwks_client() -> PyJWKClient:
    """Get or create cached JWKS client."""
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(AUTH0_JWKS_URL, cache_keys=True)
    return _jwks_client


def validate_auth0_token(token: str) -> dict:
    """
    Validate Auth0 JWT and return claims.

    Args:
        token: JWT token string (without 'Bearer ' prefix)

    Returns:
        Dictionary containing validated token claims including:
        - sub: User ID
        - scope: Granted scopes (space-separated string)
        - Custom claims (subscription_tier, roles, fga_user_id)

    Raises:
        AuthError: If token is invalid, expired, or has wrong audience/issuer
    """
    # Check cache first
    if token in _token_cache:
        return _token_cache[token]

    try:
        jwks_client = get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=AUTH0_API_AUDIENCE,
            issuer=f"https://{AUTH0_DOMAIN}/"
        )

        # Cache successful validation
        _token_cache[token] = claims

        return claims

    except jwt.ExpiredSignatureError:
        raise AuthError("Token has expired", 401)
    except jwt.InvalidAudienceError:
        raise AuthError(f"Invalid audience. Expected: {AUTH0_API_AUDIENCE}", 401)
    except jwt.InvalidIssuerError:
        raise AuthError(f"Invalid issuer. Expected: https://{AUTH0_DOMAIN}/", 401)
    except jwt.InvalidTokenError as e:
        raise AuthError(f"Invalid token: {str(e)}", 401)
    except Exception as e:
        raise AuthError(f"Token validation failed: {str(e)}", 401)


def extract_bearer_token(authorization_header: str) -> str:
    """
    Extract token from Authorization header.

    Args:
        authorization_header: Full Authorization header value

    Returns:
        Token string without 'Bearer ' prefix

    Raises:
        AuthError: If header is missing or malformed
    """
    if not authorization_header:
        raise AuthError("Authorization header is missing", 401)

    parts = authorization_header.split()

    if len(parts) != 2:
        raise AuthError("Authorization header must be 'Bearer <token>'", 401)

    if parts[0].lower() != "bearer":
        raise AuthError("Authorization header must start with 'Bearer'", 401)

    return parts[1]


def get_user_context(claims: dict) -> dict:
    """
    Extract user context from validated token claims.

    Args:
        claims: Validated JWT claims dictionary

    Returns:
        User context dictionary with:
        - user_id: Auth0 user ID (sub claim)
        - fga_user_id: FGA-formatted user ID
        - subscription_tier: User's subscription level (basic/premium)
        - roles: List of user roles
        - scopes: List of granted scopes
    """
    user_id = claims.get("sub", "")

    # Extract custom claims (with namespace prefix)
    subscription_tier = claims.get(f"{CLAIMS_NAMESPACE}/subscription_tier", "basic")
    roles = claims.get(f"{CLAIMS_NAMESPACE}/roles", [])
    fga_user_id = claims.get(f"{CLAIMS_NAMESPACE}/fga_user_id", user_id)

    # Parse scopes from space-separated string
    scopes = claims.get("scope", "").split() if claims.get("scope") else []

    return {
        "user_id": user_id,
        "fga_user_id": fga_user_id,
        "subscription_tier": subscription_tier,
        "roles": roles if isinstance(roles, list) else [roles],
        "scopes": scopes
    }


def require_scope(claims: dict, required_scope: str) -> bool:
    """
    Check if token has required scope.

    Args:
        claims: Validated JWT claims
        required_scope: Scope to check for

    Returns:
        True if scope is present

    Raises:
        AuthError: If scope is missing
    """
    scopes = claims.get("scope", "").split()
    if required_scope not in scopes:
        raise AuthError(f"Missing required scope: {required_scope}", 403)
    return True
