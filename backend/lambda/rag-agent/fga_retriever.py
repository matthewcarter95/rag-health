"""
FGA-Filtered Retriever Module

LangChain retriever that filters documents based on Auth0 FGA authorization checks.

Subscription tiers and content-to-tag mappings are stored as permanent FGA tuples.
Roles from the JWT are passed as contextual tuples at check time.

Authorization flow:
  STORED in FGA:
    - user:<id> subscriber subscription_tier:<tier>
    - content_tag:<tag> tagged content:<id>
    - subscription_tier:<tier>#subscriber viewer content_tag:<tag>
    - role:<role>#member viewer content_tag:<tag>

  CONTEXTUAL at check time (from JWT):
    - user:<id> member role:<normalized_role>
"""

import os
import time
from typing import List, Optional, Any, Dict

import requests
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from pydantic import Field

# FGA Configuration
FGA_API_URL = os.environ.get("FGA_API_URL") or "https://api.us1.fga.dev"
FGA_STORE_ID = os.environ.get("FGA_STORE_ID", "")
FGA_MODEL_ID = os.environ.get("FGA_MODEL_ID", "")
FGA_CLIENT_ID = os.environ.get("FGA_CLIENT_ID", "")
FGA_CLIENT_SECRET = os.environ.get("FGA_CLIENT_SECRET", "")
FGA_TOKEN_URL = "https://auth.fga.dev/oauth/token"
FGA_API_AUDIENCE = "https://api.us1.fga.dev/"

# Token cache
_fga_token_cache: Dict[str, Any] = {"token": None, "expires_at": 0}

# Map content tags to tier tags for local fallback only
TAG_TO_TIER_MAP = {
    "basic": "basic",
    "patient-education": "basic",
    "premium": "premium",
    "advanced": "premium",
    "clinical": "clinical",
    "research": "research",
}


def _normalize_role(role: str) -> str:
    """Normalize Auth0 role names to FGA role identifiers (lowercase, underscored)."""
    return role.lower().replace(" ", "_").replace("-", "_")


def _get_fga_access_token() -> str:
    """Get FGA access token, using cache if valid."""
    global _fga_token_cache

    if _fga_token_cache["token"] and _fga_token_cache["expires_at"] > time.time() + 60:
        return _fga_token_cache["token"]

    response = requests.post(
        FGA_TOKEN_URL,
        json={
            "client_id": FGA_CLIENT_ID,
            "client_secret": FGA_CLIENT_SECRET,
            "audience": FGA_API_AUDIENCE,
            "grant_type": "client_credentials",
        },
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    _fga_token_cache["token"] = data["access_token"]
    _fga_token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)

    return _fga_token_cache["token"]


def get_user_subscription_from_fga(user_id: str) -> str:
    """
    Look up a user's subscription tier from stored FGA tuples.

    Checks if the user is a subscriber of subscription_tier:premium.
    Falls back to "basic" on any error or if FGA is not configured.

    Args:
        user_id: Auth0 user ID (with or without "user:" prefix)

    Returns:
        "premium" or "basic"
    """
    if not FGA_API_URL or not FGA_STORE_ID or not FGA_CLIENT_ID:
        print("[FGA] Subscription lookup skipped — FGA not configured, defaulting to basic")
        return "basic"

    fga_user = f"user:{user_id}" if not user_id.startswith("user:") else user_id

    try:
        token = _get_fga_access_token()
        url = f"{FGA_API_URL}/stores/{FGA_STORE_ID}/check"

        body = {
            "tuple_key": {
                "user": fga_user,
                "relation": "subscriber",
                "object": "subscription_tier:premium",
            }
        }
        if FGA_MODEL_ID:
            body["authorization_model_id"] = FGA_MODEL_ID

        response = requests.post(
            url,
            json=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        tier = "premium" if result.get("allowed", False) else "basic"
        print(f"[FGA] Subscription lookup for {fga_user}: {tier}")
        return tier

    except Exception as e:
        print(f"[FGA] Subscription lookup failed for {user_id}: {e} — defaulting to basic")
        return "basic"


def _get_content_tier_tag(doc: Document) -> Optional[str]:
    """
    Determine the primary tier tag for a document (used for local fallback only).

    Args:
        doc: LangChain document with metadata

    Returns:
        Tier tag string or "basic" as default
    """
    tags = doc.metadata.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]

    for tag in tags:
        tag_lower = tag.lower()
        if tag_lower in TAG_TO_TIER_MAP:
            return TAG_TO_TIER_MAP[tag_lower]

    return "basic"


class FGAFilteredRetriever(BaseRetriever):
    """
    Retriever that wraps another retriever and filters results via Auth0 FGA.

    Subscription and content-tag relationships are stored as permanent FGA tuples.
    Role membership is passed as contextual tuples from the JWT at check time.
    """

    base_retriever: BaseRetriever = Field(description="The underlying retriever to wrap")
    user_id: str = Field(description="The user ID for FGA checks (format: user:<auth0_id>)")
    subscription_tier: str = Field(default="basic", description="User's subscription tier (used for local fallback only)")
    roles: List[str] = Field(default_factory=list, description="User's roles from JWT")
    relation: str = Field(default="viewer", description="The FGA relation to check")
    object_type: str = Field(default="content", description="The FGA object type prefix")

    class Config:
        arbitrary_types_allowed = True

    def _check_permission_local(self, content_tier_tag: str) -> bool:
        """Local tier-based authorization check (fallback when FGA is not configured)."""
        normalized_roles = [_normalize_role(r) for r in self.roles]
        if any(role in normalized_roles for role in ["researcher", "clinical_reviewer", "healthcare_provider"]):
            return True

        tier_levels = {"basic": 1, "premium": 2, "advanced": 2, "clinical": 3, "research": 3}
        user_level = tier_levels.get(self.subscription_tier, 1)
        content_level = tier_levels.get(content_tier_tag, 1)
        return user_level >= content_level

    def _check_permission_with_fga(self, user_id: str, object_id: str) -> bool:
        """
        Check permission via FGA.

        Subscription and content-tag tuples are stored permanently in FGA.
        Only role membership is passed as a contextual tuple (sourced from JWT).

        Args:
            user_id: FGA user identifier (format: user:<id>)
            object_id: FGA object identifier (format: content:<content_id>)
        """
        if not FGA_API_URL or not FGA_STORE_ID or not FGA_CLIENT_ID:
            content_tier_tag = "basic"
            allowed = self._check_permission_local(content_tier_tag)
            print(f"[FGA] Local fallback: {user_id} -> {object_id} = {allowed}")
            return allowed

        try:
            token = _get_fga_access_token()
            url = f"{FGA_API_URL}/stores/{FGA_STORE_ID}/check"

            # Only pass role contextual tuples — subscription + content tags are stored in FGA
            contextual_tuples = []
            for role in self.roles:
                normalized = _normalize_role(role)
                contextual_tuples.append({
                    "user": user_id,
                    "relation": "member",
                    "object": f"role:{normalized}",
                })

            body: Dict[str, Any] = {
                "tuple_key": {
                    "user": user_id,
                    "relation": self.relation,
                    "object": object_id,
                },
            }
            if contextual_tuples:
                body["contextual_tuples"] = {"tuple_keys": contextual_tuples}
            if FGA_MODEL_ID:
                body["authorization_model_id"] = FGA_MODEL_ID

            response = requests.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
            response.raise_for_status()
            result = response.json()

            allowed = result.get("allowed", False)
            print(f"[FGA] Check: {user_id} (roles={self.roles}) -> {self.relation} -> {object_id} = {allowed}")
            return allowed

        except Exception as e:
            print(f"[FGA] Check failed for {user_id} -> {object_id}: {e}")
            return False

    def _get_fga_object_id(self, doc: Document) -> Optional[str]:
        """Extract FGA object ID from document metadata."""
        if "fga_object_id" in doc.metadata:
            return doc.metadata["fga_object_id"]
        if "content_id" in doc.metadata:
            return f"{self.object_type}:{doc.metadata['content_id']}"
        return None

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> List[Document]:
        """Retrieve and filter documents by FGA authorization."""
        if hasattr(self.base_retriever, "invoke"):
            base_docs = self.base_retriever.invoke(query)
        else:
            base_docs = self.base_retriever.get_relevant_documents(query)

        print(f"[FGA] Filtering {len(base_docs)} docs for user {self.user_id} (roles={self.roles})")

        fga_user = f"user:{self.user_id}" if not self.user_id.startswith("user:") else self.user_id

        authorized_docs = []
        denied_count = 0

        for doc in base_docs:
            object_id = self._get_fga_object_id(doc)
            if object_id is None:
                print(f"[FGA] Warning: missing FGA object ID on doc: {doc.metadata}")
                continue

            if self._check_permission_with_fga(fga_user, object_id):
                authorized_docs.append(doc)
            else:
                denied_count += 1

        if denied_count:
            print(f"[FGA] Filtered out {denied_count} unauthorized docs for {self.user_id}")
        print(f"[FGA] Authorized {len(authorized_docs)} docs for {self.user_id}")
        return authorized_docs


def create_fga_retriever(
    base_retriever: BaseRetriever,
    user_id: str,
    subscription_tier: str = "basic",
    roles: Optional[List[str]] = None,
    relation: str = "viewer",
    object_type: str = "content",
) -> FGAFilteredRetriever:
    """
    Factory function to create an FGA-filtered retriever.

    Args:
        base_retriever: The underlying retriever (e.g., FAISS retriever)
        user_id: Auth0 user ID
        subscription_tier: Cached subscription tier (for local fallback only)
        roles: User's roles from JWT
        relation: FGA relation to check (default: 'viewer')
        object_type: FGA object type prefix (default: 'content')
    """
    return FGAFilteredRetriever(
        base_retriever=base_retriever,
        user_id=user_id,
        subscription_tier=subscription_tier,
        roles=roles or [],
        relation=relation,
        object_type=object_type,
    )
