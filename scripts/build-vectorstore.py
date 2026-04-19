#!/usr/bin/env python3
"""
Build FAISS Vector Store from Content JSON Files

This script loads gut health content from JSON files, creates embeddings using
Amazon Bedrock Titan, and builds a FAISS vector store for RAG retrieval.

Usage:
    python build-vectorstore.py [--output-dir /path/to/output]

Requirements:
    - AWS credentials configured with Bedrock access
    - langchain, langchain-aws, langchain-community, faiss-cpu packages
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List

# Add the lambda directory to path for local development
sys.path.insert(0, str(Path(__file__).parent.parent / "backend" / "lambda" / "rag-agent"))

from langchain_core.documents import Document
from langchain_aws import BedrockEmbeddings
from langchain_community.vectorstores import FAISS


# Configuration
CONTENT_DIR = Path(__file__).parent.parent / "content" / "data"
DEFAULT_OUTPUT_DIR = Path(__file__).parent.parent / "content" / "vectorstore"
EMBEDDINGS_MODEL_ID = "amazon.titan-embed-text-v2:0"
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Content files to process
CONTENT_FILES = [
    "microbiome.json",
    "probiotics.json",
    "digestive-disorders.json",
    "nutrition.json",
    "gut-brain-axis.json",
]


def load_content_file(filepath: Path) -> List[Document]:
    """
    Load content from a JSON file and convert to LangChain Documents.

    Args:
        filepath: Path to JSON content file

    Returns:
        List of LangChain Document objects
    """
    print(f"Loading content from {filepath.name}...")

    with open(filepath, "r", encoding="utf-8") as f:
        content_items = json.load(f)

    documents = []
    for item in content_items:
        # Create document with content as page_content
        doc = Document(
            page_content=item["content"],
            metadata={
                "content_id": item["content_id"],
                "title": item["title"],
                "topic": item["topic"],
                "tags": item["tags"],
                "fga_object_id": item["fga_object_id"],
                "summary": item.get("summary", ""),
            }
        )
        documents.append(doc)

    print(f"  Loaded {len(documents)} documents from {filepath.name}")
    return documents


def load_all_content() -> List[Document]:
    """
    Load all content from JSON files in the content directory.

    Returns:
        List of all LangChain Document objects
    """
    all_documents = []

    for filename in CONTENT_FILES:
        filepath = CONTENT_DIR / filename
        if filepath.exists():
            documents = load_content_file(filepath)
            all_documents.extend(documents)
        else:
            print(f"Warning: Content file not found: {filepath}")

    print(f"\nTotal documents loaded: {len(all_documents)}")
    return all_documents


def create_embeddings() -> BedrockEmbeddings:
    """
    Create Bedrock embeddings model.

    Returns:
        BedrockEmbeddings instance
    """
    print(f"\nInitializing Bedrock embeddings ({EMBEDDINGS_MODEL_ID})...")
    return BedrockEmbeddings(
        model_id=EMBEDDINGS_MODEL_ID,
        region_name=AWS_REGION,
    )


def build_vectorstore(documents: List[Document], embeddings: BedrockEmbeddings, output_dir: Path) -> FAISS:
    """
    Build and save FAISS vector store from documents.

    Args:
        documents: List of LangChain Document objects
        embeddings: Bedrock embeddings model
        output_dir: Directory to save the vector store

    Returns:
        FAISS vector store instance
    """
    print(f"\nBuilding FAISS vector store with {len(documents)} documents...")

    # Create vector store
    vectorstore = FAISS.from_documents(documents, embeddings)

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save vector store
    index_path = output_dir / "gut_health"
    print(f"Saving vector store to {index_path}...")
    vectorstore.save_local(str(index_path))

    print(f"✅ Vector store saved to {index_path}")
    return vectorstore


def verify_vectorstore(vectorstore: FAISS) -> None:
    """
    Verify the vector store works with a test query.

    Args:
        vectorstore: FAISS vector store to test
    """
    print("\nVerifying vector store with test queries...")

    test_queries = [
        "What is the gut microbiome?",
        "How do probiotics work?",
        "What is IBS?",
        "Foods for gut health",
        "Gut brain connection",
    ]

    for query in test_queries:
        results = vectorstore.similarity_search(query, k=2)
        print(f"\n  Query: '{query}'")
        for i, doc in enumerate(results, 1):
            title = doc.metadata.get("title", "Unknown")
            tags = doc.metadata.get("tags", [])
            print(f"    {i}. {title} [{', '.join(tags)}]")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Build FAISS vector store from gut health content"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to save the vector store",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip verification step",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("RAG Health Vector Store Builder")
    print("=" * 60)

    # Load all content
    documents = load_all_content()

    if not documents:
        print("Error: No documents loaded. Check content directory.")
        sys.exit(1)

    # Create embeddings model
    embeddings = create_embeddings()

    # Build and save vector store
    vectorstore = build_vectorstore(documents, embeddings, args.output_dir)

    # Verify
    if not args.skip_verify:
        verify_vectorstore(vectorstore)

    print("\n" + "=" * 60)
    print("✅ Vector store build complete!")
    print("=" * 60)

    # Print summary
    print(f"\nSummary:")
    print(f"  Documents indexed: {len(documents)}")
    print(f"  Output location: {args.output_dir / 'gut_health'}")
    print(f"  Files created: gut_health.faiss, gut_health.pkl")

    print("\nTo use in Lambda:")
    print("  1. Upload vector store files to S3")
    print("  2. Download to /tmp in Lambda or use S3 directly")
    print("  3. Load with: FAISS.load_local('/tmp/vectorstore/gut_health', embeddings)")


if __name__ == "__main__":
    main()
