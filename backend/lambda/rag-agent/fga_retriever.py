"""
FGA-Filtered Retriever Module

LangChain retriever that filters documents based on Auth0 FGA authorization checks.
"""

import os
from typing import List, Optional, Any

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from openfga_sdk import OpenFgaClient, ClientConfiguration
from openfga_sdk.client.models import ClientCheckRequest
from pydantic import Field

# FGA Configuration
FGA_API_URL = os.environ.get("FGA_API_URL", "")
FGA_STORE_ID = os.environ.get("FGA_STORE_ID", "")
FGA_MODEL_ID = os.environ.get("FGA_MODEL_ID", "")


class FGAFilteredRetriever(BaseRetriever):
    """
    A retriever that wraps another retriever and filters results based on FGA authorization.

    For each document retrieved, checks if the user has the specified relation
    (default: "viewer") to the document's FGA object.
    """

    base_retriever: BaseRetriever = Field(description="The underlying retriever to wrap")
    user_id: str = Field(description="The user ID for FGA checks (format: user:<auth0_id>)")
    relation: str = Field(default="viewer", description="The FGA relation to check")
    object_type: str = Field(default="content", description="The FGA object type prefix")
    fga_client: Optional[Any] = Field(default=None, description="FGA client instance")

    class Config:
        arbitrary_types_allowed = True

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.fga_client is None:
            self.fga_client = self._create_fga_client()

    def _create_fga_client(self) -> OpenFgaClient:
        """Create and configure FGA client."""
        configuration = ClientConfiguration(
            api_url=FGA_API_URL,
            store_id=FGA_STORE_ID,
            authorization_model_id=FGA_MODEL_ID,
        )
        return OpenFgaClient(configuration)

    async def _check_permission(self, user_id: str, object_id: str) -> bool:
        """
        Check if user has permission to access the object.

        Args:
            user_id: FGA user identifier (format: user:<id>)
            object_id: FGA object identifier (format: content:<content_id>)

        Returns:
            True if user has the required relation to the object
        """
        try:
            request = ClientCheckRequest(
                user=user_id,
                relation=self.relation,
                object=object_id,
            )
            response = await self.fga_client.check(request)
            return response.allowed
        except Exception as e:
            # Log error but deny access on failure (fail-closed)
            print(f"FGA check failed for {user_id} -> {object_id}: {e}")
            return False

    def _check_permission_sync(self, user_id: str, object_id: str) -> bool:
        """Synchronous version of permission check."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(self._check_permission(user_id, object_id))

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
        Retrieve documents and filter by FGA authorization.

        Args:
            query: Search query string
            run_manager: Callback manager for the retriever run

        Returns:
            List of documents the user is authorized to view
        """
        # Get documents from base retriever
        base_docs = self.base_retriever.get_relevant_documents(query)

        # Filter by FGA authorization
        authorized_docs = []
        denied_count = 0

        for doc in base_docs:
            object_id = self._get_fga_object_id(doc)

            if object_id is None:
                # Skip documents without FGA object ID
                print(f"Warning: Document missing FGA object ID, skipping")
                continue

            # Format user ID for FGA
            fga_user = f"user:{self.user_id}" if not self.user_id.startswith("user:") else self.user_id

            if self._check_permission_sync(fga_user, object_id):
                authorized_docs.append(doc)
            else:
                denied_count += 1

        if denied_count > 0:
            print(f"FGA filtered out {denied_count} documents for user {self.user_id}")

        return authorized_docs

    async def _aget_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> List[Document]:
        """Async version of document retrieval with FGA filtering."""
        # Get documents from base retriever (may need async support)
        if hasattr(self.base_retriever, 'aget_relevant_documents'):
            base_docs = await self.base_retriever.aget_relevant_documents(query)
        else:
            base_docs = self.base_retriever.get_relevant_documents(query)

        # Filter by FGA authorization
        authorized_docs = []
        denied_count = 0

        for doc in base_docs:
            object_id = self._get_fga_object_id(doc)

            if object_id is None:
                continue

            fga_user = f"user:{self.user_id}" if not self.user_id.startswith("user:") else self.user_id

            if await self._check_permission(fga_user, object_id):
                authorized_docs.append(doc)
            else:
                denied_count += 1

        if denied_count > 0:
            print(f"FGA filtered out {denied_count} documents for user {self.user_id}")

        return authorized_docs


def create_fga_retriever(
    base_retriever: BaseRetriever,
    user_id: str,
    relation: str = "viewer",
    object_type: str = "content",
    fga_client: Optional[Any] = None,
) -> FGAFilteredRetriever:
    """
    Factory function to create an FGA-filtered retriever.

    Args:
        base_retriever: The underlying retriever (e.g., FAISS retriever)
        user_id: Auth0 user ID (will be prefixed with 'user:' if needed)
        relation: FGA relation to check (default: 'viewer')
        object_type: FGA object type prefix (default: 'content')
        fga_client: Optional pre-configured FGA client

    Returns:
        FGAFilteredRetriever instance
    """
    return FGAFilteredRetriever(
        base_retriever=base_retriever,
        user_id=user_id,
        relation=relation,
        object_type=object_type,
        fga_client=fga_client,
    )
