"""
LangChain RAG Chains Module

Defines RAG chains for gut health content retrieval with Bedrock Claude.
"""

import os
from pathlib import Path
from typing import Optional, List, Dict, Any

import boto3
from langchain_aws import ChatBedrock, BedrockEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.documents import Document

from fga_retriever import create_fga_retriever

# Configuration
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")
EMBEDDINGS_MODEL_ID = "amazon.titan-embed-text-v2:0"
VECTORSTORE_PATH = os.environ.get("VECTORSTORE_PATH", "/tmp/vectorstore")
AWS_REGION = os.environ.get("AWS_REGION_NAME", "us-east-1")
S3_CONTENT_BUCKET = os.environ.get("S3_CONTENT_BUCKET", "")
S3_VECTORSTORE_PREFIX = os.environ.get("S3_VECTORSTORE_PREFIX", "vectorstore/gut_health")

# Track if vectorstore has been downloaded (persist across warm Lambda invocations)
_vectorstore_downloaded = False


def download_vectorstore_from_s3(
    bucket: str,
    prefix: str,
    local_path: str,
) -> bool:
    """
    Download vectorstore files from S3 to local path.

    Args:
        bucket: S3 bucket name
        prefix: S3 prefix for vectorstore files
        local_path: Local directory to download to

    Returns:
        True if download successful, False otherwise
    """
    global _vectorstore_downloaded

    # Skip if already downloaded (warm Lambda)
    if _vectorstore_downloaded and Path(local_path).exists():
        print(f"Vectorstore already exists at {local_path}, skipping download")
        return True

    if not bucket:
        print("Warning: S3_CONTENT_BUCKET not configured, cannot download vectorstore")
        return False

    try:
        s3_client = boto3.client("s3", region_name=AWS_REGION)

        # Create local directory
        Path(local_path).mkdir(parents=True, exist_ok=True)

        # List and download all files in the vectorstore prefix
        paginator = s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=bucket, Prefix=prefix)

        downloaded_files = []
        for page in pages:
            for obj in page.get("Contents", []):
                s3_key = obj["Key"]
                # Get filename relative to prefix
                filename = s3_key[len(prefix):].lstrip("/")
                if not filename:
                    continue

                local_file = Path(local_path) / filename
                local_file.parent.mkdir(parents=True, exist_ok=True)

                print(f"Downloading s3://{bucket}/{s3_key} to {local_file}")
                s3_client.download_file(bucket, s3_key, str(local_file))
                downloaded_files.append(filename)

        if downloaded_files:
            print(f"Successfully downloaded {len(downloaded_files)} vectorstore files: {downloaded_files}")
            _vectorstore_downloaded = True
            return True
        else:
            print(f"No vectorstore files found at s3://{bucket}/{prefix}")
            return False

    except Exception as e:
        print(f"Error downloading vectorstore from S3: {e}")
        return False

# System prompt for the gut health assistant
SYSTEM_PROMPT = """You are a knowledgeable gut health assistant powered by RAG Health. Your role is to provide accurate, helpful information about gut health topics including:
- The gut microbiome and its role in health
- Probiotics, prebiotics, and fermented foods
- Digestive disorders and their management
- Nutrition for gut health
- The gut-brain connection

Guidelines:
1. Base your responses on the provided context from our content repository
2. If the context doesn't contain relevant information, acknowledge this and provide general guidance while recommending the user consult a healthcare provider
3. Always encourage users to consult healthcare professionals for medical advice
4. Be empathetic and supportive when discussing health concerns
5. Cite specific content when relevant (e.g., "According to our guide on probiotics...")
6. If content access is limited due to subscription tier, mention that premium or clinical content may be available with an upgraded subscription

Remember: You can only access content the user is authorized to view based on their subscription and roles."""

RAG_PROMPT_TEMPLATE = """Based on the following context from our gut health content repository, please answer the user's question.

Context:
{context}

User Question: {question}

If the context doesn't contain relevant information for this question, acknowledge that and provide helpful general guidance while recommending consultation with a healthcare provider.

Response:"""


class RagHealthChain:
    """RAG chain for gut health content retrieval and response generation."""

    def __init__(
        self,
        vectorstore_path: Optional[str] = None,
        user_id: Optional[str] = None,
        subscription_tier: str = "basic",
        roles: Optional[List[str]] = None,
    ):
        """
        Initialize the RAG chain.

        Args:
            vectorstore_path: Path to FAISS vectorstore
            user_id: User ID for FGA authorization (if None, no FGA filtering)
            subscription_tier: User's subscription tier from JWT (basic, premium)
            roles: User's roles from JWT for ABAC
        """
        self.vectorstore_path = vectorstore_path or VECTORSTORE_PATH
        self.user_id = user_id
        self.subscription_tier = subscription_tier
        self.roles = roles or []

        # Initialize components
        self.embeddings = self._create_embeddings()
        self.llm = self._create_llm()
        self.vectorstore = self._load_vectorstore()
        self.retriever = self._create_retriever()
        self.chain = self._create_chain()

    def _create_embeddings(self) -> BedrockEmbeddings:
        """Create Bedrock embeddings model."""
        return BedrockEmbeddings(
            model_id=EMBEDDINGS_MODEL_ID,
            region_name=AWS_REGION,
        )

    def _create_llm(self) -> ChatBedrock:
        """Create Bedrock Claude LLM."""
        return ChatBedrock(
            model_id=BEDROCK_MODEL_ID,
            region_name=AWS_REGION,
            model_kwargs={
                "max_tokens": 2048,
                "temperature": 0.7,
            },
        )

    def _load_vectorstore(self) -> Optional[FAISS]:
        """Load FAISS vectorstore from disk, downloading from S3 if needed."""
        # Download from S3 if not already present
        download_vectorstore_from_s3(
            bucket=S3_CONTENT_BUCKET,
            prefix=S3_VECTORSTORE_PREFIX,
            local_path=self.vectorstore_path,
        )

        try:
            return FAISS.load_local(
                self.vectorstore_path,
                self.embeddings,
                allow_dangerous_deserialization=True,
            )
        except Exception as e:
            print(f"Warning: Could not load vectorstore from {self.vectorstore_path}: {e}")
            return None

    def _create_retriever(self):
        """Create retriever with optional FGA filtering."""
        if self.vectorstore is None:
            return None

        base_retriever = self.vectorstore.as_retriever(
            search_type="similarity",
            search_kwargs={"k": 5},
        )

        # Wrap with FGA ABAC filtering if user_id is provided
        if self.user_id:
            return create_fga_retriever(
                base_retriever=base_retriever,
                user_id=self.user_id,
                subscription_tier=self.subscription_tier,
                roles=self.roles,
                relation="viewer",
                object_type="content",
            )

        return base_retriever

    def _format_docs(self, docs: List[Document]) -> str:
        """Format retrieved documents into context string."""
        if not docs:
            return "No relevant content found in the accessible knowledge base."

        formatted = []
        for i, doc in enumerate(docs, 1):
            title = doc.metadata.get("title", "Untitled")
            topic = doc.metadata.get("topic", "general")
            tags = doc.metadata.get("tags", [])
            content = doc.page_content

            formatted.append(
                f"[{i}] {title}\n"
                f"Topic: {topic} | Tags: {', '.join(tags)}\n"
                f"{content}\n"
            )

        return "\n---\n".join(formatted)

    def _create_chain(self):
        """Create the RAG chain."""
        if self.retriever is None:
            # Return a chain that just uses the LLM without retrieval
            prompt = ChatPromptTemplate.from_messages([
                ("system", SYSTEM_PROMPT),
                ("human", "{question}"),
            ])
            return prompt | self.llm | StrOutputParser()

        # Create RAG prompt
        prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT),
            ("human", RAG_PROMPT_TEMPLATE),
        ])

        # Build the chain
        chain = (
            {
                "context": self.retriever | RunnableLambda(self._format_docs),
                "question": RunnablePassthrough(),
            }
            | prompt
            | self.llm
            | StrOutputParser()
        )

        return chain

    def invoke(self, question: str) -> str:
        """
        Run the RAG chain with a question.

        Args:
            question: User's question about gut health

        Returns:
            Generated response based on authorized content
        """
        return self.chain.invoke(question)

    async def ainvoke(self, question: str) -> str:
        """Async version of invoke."""
        return await self.chain.ainvoke(question)

    def get_relevant_docs(self, query: str) -> List[Document]:
        """
        Get relevant documents for a query (for debugging/inspection).

        Args:
            query: Search query

        Returns:
            List of relevant documents (filtered by FGA if applicable)
        """
        if self.retriever is None:
            return []
        if hasattr(self.retriever, 'invoke'):
            return self.retriever.invoke(query)
        return self.retriever.get_relevant_documents(query)


def create_rag_chain(
    user_id: str,
    subscription_tier: str = "basic",
    roles: Optional[List[str]] = None,
    vectorstore_path: Optional[str] = None,
) -> RagHealthChain:
    """
    Factory function to create a RAG chain for a specific user with ABAC.

    Args:
        user_id: Auth0 user ID for FGA authorization
        subscription_tier: User's subscription tier from JWT (basic, premium)
        roles: User's roles from JWT (healthcare_provider, researcher, clinical_reviewer)
        vectorstore_path: Optional custom vectorstore path

    Returns:
        Configured RagHealthChain instance
    """
    return RagHealthChain(
        vectorstore_path=vectorstore_path,
        user_id=user_id,
        subscription_tier=subscription_tier,
        roles=roles,
    )
