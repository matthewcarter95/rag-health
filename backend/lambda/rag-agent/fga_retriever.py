"""
FGA-Filtered Retriever Module

LangChain retriever that filters documents based on Auth0 FGA authorization checks.
Uses ABAC model with contextual tuples for subscription tier and content tags.
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
FGA_API_URL = os.environ.get("FGA_API_URL", "")
FGA_STORE_ID = os.environ.get("FGA_STORE_ID", "")
FGA_MODEL_ID = os.environ.get("FGA_MODEL_ID", "")
FGA_CLIENT_ID = os.environ.get("FGA_CLIENT_ID", "")
FGA_CLIENT_SECRET = os.environ.get("FGA_CLIENT_SECRET", "")
FGA_TOKEN_URL = "https://fga.us.auth0.com/oauth/token"
FGA_API_AUDIENCE = "https://api.us1.fga.dev/"

# Token cache
_fga_token_cache: Dict[str, Any] = {"token": None, "expires_at": 0}

# Map content tags to tier tags for ABAC
TAG_TO_TIER_MAP = {
    "basic": "basic",
    "patient-education": "basic",
    "premium": "premium",
    "advanced": "premium",
    "clinical": "clinical",
    "research": "research",
}


def _get_fga_access_token() -> str:
    """Get FGA access token, using cache if valid."""
    global _fga_token_cache

    # Check if cached token is still valid (with 60s buffer)
    if _fga_token_cache["token"] and _fga_token_cache["expires_at"] > time.time() + 60:
        return _fga_token_cache["token"]

    # Request new token
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


def _get_content_tier_tag(doc: Document) -> Optional[str]:
    """
    Determine the tier tag for a document based on its metadata tags.

    Args:
        doc: LangChain document with metadata

    Returns:
        Tier tag (basic, premium, clinical, research) or None
    """
    tags = doc.metadata.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]

    # Check tags in priority order (most restrictive first)
    for tag in tags:
        tag_lower = tag.lower()
        if tag_lower in TAG_TO_TIER_MAP:
            return TAG_TO_TIER_MAP[tag_lower]

    # Default to basic if no matching tag found
    return "basic"


class FGAFilteredRetriever(BaseRetriever):
    """
    A retriever that wraps another retriever and filters results based on FGA authorization.
    Uses ABAC model with contextual tuples for dynamic permission checks.

    For each document retrieved, checks if the user has permission based on:
    1. User's subscription tier (from JWT, passed as contextual tuple)
    2. User's roles (from JWT, passed as contextual tuple)
    3. Content's tier tag (from document metadata, passed as contextual tuple)
    """

    base_retriever: BaseRetriever = Field(description="The underlying retriever to wrap")
    user_id: str = Field(description="The user ID for FGA checks (format: user:<auth0_id>)")
    subscription_tier: str = Field(default="basic", description="User's subscription tier from JWT")
    roles: List[str] = Field(default_factory=list, description="User's roles from JWT")
    relation: str = Field(default="viewer", description="The FGA relation to check")
    object_type: str = Field(default="content", description="The FGA object type prefix")

    class Config:
        arbitrary_types_allowed = True

    def _check_permission_with_context(
        self,
        user_id: str,
        object_id: str,
        content_tier_tag: str,
    ) -> bool:
        """
        Check if user has permission using contextual tuples for ABAC.

        Contextual tuples passed at check time:
        1. user:<id> is subscriber of subscription_tier:<tier>
        2. content:<id> is tagged with content_tag:<tier_tag>

        Args:
            user_id: FGA user identifier (format: user:<id>)
            object_id: FGA object identifier (format: content:<content_id>)
            content_tier_tag: The tier tag for this content (basic, premium, clinical, research)

        Returns:
            True if user has the required relation to the object
        """
        try:
            token = _get_fga_access_token()
            url = f"{FGA_API_URL}/stores/{FGA_STORE_ID}/check"

            # Build contextual tuples
            contextual_tuples = []

            # Add user -> subscription_tier relationship
            contextual_tuples.append({
                "user": user_id,
                "relation": "subscriber",
                "object": f"subscription_tier:{self.subscription_tier}",
            })

            # Add user -> role relationships
            for role in self.roles:
                contextual_tuples.append({
                    "user": user_id,
                    "relation": "member",
                    "object": f"role:{role}",
                })

            # Add content -> content_tag relationship
            contextual_tuples.append({
                "user": f"content_tag:{content_tier_tag}",
                "relation": "tagged",
                "object": object_id,
            })

            # Build request body
            body = {
                "tuple_key": {
                    "user": user_id,
                    "relation": self.relation,
                    "object": object_id,
                },
                "contextual_tuples": {
                    "tuple_keys": contextual_tuples,
                },
            }

            # Add authorization model ID if configured
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
            print(f"FGA ABAC check: {user_id} (tier={self.subscription_tier}, roles={self.roles}) -> {self.relation} -> {object_id} (tag={content_tier_tag}) = {allowed}")
            return allowed

        except Exception as e:
            # Log error but deny access on failure (fail-closed)
            print(f"FGA check failed for {user_id} -> {object_id}: {e}")
            return False

    def _get_fga_object_id(self, doc: Document) -> Optional[str]:
        """
        Extract FGA object ID from document metadata.

        Args:
            doc: LangChain document

        Returns:
            FGA object ID or None if not found
        """
        # Try direct fga_object_id field first
        if "fga_object_id" in doc.metadata:
            return doc.metadata["fga_object_id"]

        # Fall back to constructing from content_id
        if "content_id" in doc.metadata:
            return f"{self.object_type}:{doc.metadata['content_id']}"

        return None

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> List[Document]:
        """
        Retrieve documents and filter by FGA authorization using ABAC.

        Args:
            query: Search query string
            run_manager: Callback manager for the retriever run

        Returns:
            List of documents the user is authorized to view
        """
        # Get documents from base retriever
        if hasattr(self.base_retriever, 'invoke'):
            base_docs = self.base_retriever.invoke(query)
        else:
            base_docs = self.base_retriever.get_relevant_documents(query)

        print(f"FGA ABAC filtering {len(base_docs)} documents for user {self.user_id} (tier={self.subscription_tier}, roles={self.roles})")

        # Filter by FGA authorization
        authorized_docs = []
        denied_count = 0

        for doc in base_docs:
            object_id = self._get_fga_object_id(doc)

            if object_id is None:
                print(f"Warning: Document missing FGA object ID, skipping. Metadata: {doc.metadata}")
                continue

            # Get content tier tag from document metadata
            content_tier_tag = _get_content_tier_tag(doc)

            # Format user ID for FGA
            fga_user = f"user:{self.user_id}" if not self.user_id.startswith("user:") else self.user_id

            if self._check_permission_with_context(fga_user, object_id, content_tier_tag):
                authorized_docs.append(doc)
            else:
                denied_count += 1

        if denied_count > 0:
            print(f"FGA ABAC filtered out {denied_count} documents for user {self.user_id}")

        print(f"FGA ABAC authorized {len(authorized_docs)} documents for user {self.user_id}")
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
    Factory function to create an FGA-filtered retriever with ABAC support.

    Args:
        base_retriever: The underlying retriever (e.g., FAISS retriever)
        user_id: Auth0 user ID (will be prefixed with 'user:' if needed)
        subscription_tier: User's subscription tier from JWT (basic, premium)
        roles: User's roles from JWT (healthcare_provider, researcher, clinical_reviewer)
        relation: FGA relation to check (default: 'viewer')
        object_type: FGA object type prefix (default: 'content')

    Returns:
        FGAFilteredRetriever instance
    """
    return FGAFilteredRetriever(
        base_retriever=base_retriever,
        user_id=user_id,
        subscription_tier=subscription_tier,
        roles=roles or [],
        relation=relation,
        object_type=object_type,
    )
