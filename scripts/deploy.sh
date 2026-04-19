#!/bin/bash
#
# Deploy RAG Health Infrastructure
#
# This script handles the full deployment pipeline:
#   1. Build vector store from content
#   2. Deploy SAM template (Lambda + DynamoDB + S3)
#   3. Upload vector store to S3
#   4. Seed FGA tuples
#
# Prerequisites:
#   - AWS CLI configured
#   - SAM CLI installed
#   - Python 3.11+ with required packages
#   - FGA CLI installed
#
# Usage: ./deploy.sh [environment]
#   environment: dev (default), staging, prod

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SAM_DIR="${PROJECT_ROOT}/infrastructure/sam"

# Default environment
ENVIRONMENT="${1:-dev}"

echo "=" * 60
echo "RAG Health Deployment"
echo "Environment: ${ENVIRONMENT}"
echo "=" * 60
echo ""

# Check prerequisites
check_prerequisites() {
    echo "Checking prerequisites..."

    if ! command -v aws &> /dev/null; then
        echo "Error: AWS CLI not found"
        exit 1
    fi

    if ! command -v sam &> /dev/null; then
        echo "Error: SAM CLI not found. Install with: brew install aws-sam-cli"
        exit 1
    fi

    if ! command -v python3 &> /dev/null; then
        echo "Error: Python 3 not found"
        exit 1
    fi

    echo "✅ Prerequisites check passed"
    echo ""
}

# Build vector store
build_vectorstore() {
    echo "Building vector store..."
    cd "${PROJECT_ROOT}"

    # Install dependencies if needed
    if ! python3 -c "import langchain_aws" 2>/dev/null; then
        echo "Installing Python dependencies..."
        pip3 install -r requirements.txt
    fi

    python3 scripts/build-vectorstore.py
    echo ""
}

# Deploy SAM template
deploy_sam() {
    echo "Deploying SAM template..."
    cd "${SAM_DIR}"

    # Build
    sam build

    # Deploy
    sam deploy \
        --stack-name "rag-health-${ENVIRONMENT}" \
        --parameter-overrides \
            "Environment=${ENVIRONMENT}" \
            "FgaStoreId=${FGA_STORE_ID:-placeholder}" \
            "FgaApiUrl=${FGA_API_URL:-placeholder}" \
            "FgaModelId=${FGA_MODEL_ID:-placeholder}" \
        --capabilities CAPABILITY_IAM \
        --resolve-s3 \
        --no-confirm-changeset \
        --no-fail-on-empty-changeset

    echo ""
}

# Get stack outputs
get_stack_outputs() {
    STACK_NAME="rag-health-${ENVIRONMENT}"

    FUNCTION_URL=$(aws cloudformation describe-stacks \
        --stack-name "${STACK_NAME}" \
        --query 'Stacks[0].Outputs[?OutputKey==`RagAgentFunctionUrl`].OutputValue' \
        --output text)

    S3_BUCKET=$(aws cloudformation describe-stacks \
        --stack-name "${STACK_NAME}" \
        --query 'Stacks[0].Outputs[?OutputKey==`ContentBucketName`].OutputValue' \
        --output text)

    DYNAMODB_TABLE=$(aws cloudformation describe-stacks \
        --stack-name "${STACK_NAME}" \
        --query 'Stacks[0].Outputs[?OutputKey==`SubscriptionsTableName`].OutputValue' \
        --output text)

    echo "Stack Outputs:"
    echo "  Function URL: ${FUNCTION_URL}"
    echo "  S3 Bucket: ${S3_BUCKET}"
    echo "  DynamoDB Table: ${DYNAMODB_TABLE}"
    echo ""
}

# Upload vector store to S3
upload_vectorstore() {
    if [ -z "$S3_BUCKET" ]; then
        echo "Warning: S3 bucket not available, skipping vector store upload"
        return
    fi

    echo "Uploading vector store to S3..."

    VECTORSTORE_DIR="${PROJECT_ROOT}/content/vectorstore/gut_health"

    if [ -d "$VECTORSTORE_DIR" ]; then
        aws s3 sync "${VECTORSTORE_DIR}" "s3://${S3_BUCKET}/vectorstore/gut_health/"
        echo "✅ Vector store uploaded to s3://${S3_BUCKET}/vectorstore/"
    else
        echo "Warning: Vector store not found at ${VECTORSTORE_DIR}"
        echo "Run 'python scripts/build-vectorstore.py' first"
    fi

    echo ""
}

# Seed FGA tuples
seed_fga_tuples() {
    if [ -z "$FGA_STORE_ID" ]; then
        echo "Warning: FGA_STORE_ID not set, skipping FGA tuple seeding"
        echo "Set FGA_STORE_ID and run: ./scripts/seed-fga-tuples.sh"
        return
    fi

    echo "Seeding FGA tuples..."
    "${SCRIPT_DIR}/seed-fga-tuples.sh"
    echo ""
}

# Print summary
print_summary() {
    echo "=" * 60
    echo "✅ Deployment Complete!"
    echo "=" * 60
    echo ""
    echo "RAG Health API Endpoint:"
    echo "  ${FUNCTION_URL}"
    echo ""
    echo "Test the API:"
    echo "  curl -X POST ${FUNCTION_URL}health \\"
    echo "    -H 'Authorization: Bearer <your-auth0-token>'"
    echo ""
    echo "Query gut health content:"
    echo "  curl -X POST ${FUNCTION_URL}query \\"
    echo "    -H 'Authorization: Bearer <your-auth0-token>' \\"
    echo "    -H 'Content-Type: application/json' \\"
    echo "    -d '{\"query\": \"What is the gut microbiome?\"}'"
    echo ""
    echo "Next steps:"
    echo "  1. Create Auth0 API: ./scripts/create-auth0-api.sh"
    echo "  2. Deploy Auth0 Action for custom claims"
    echo "  3. Configure Google Social Connector for calendar access"
    echo "  4. Test with different user subscription tiers"
}

# Main execution
main() {
    check_prerequisites

    # Optional: Build vector store (can be skipped if already built)
    read -p "Build vector store? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        build_vectorstore
    fi

    # Deploy SAM template
    deploy_sam

    # Get stack outputs
    get_stack_outputs

    # Upload vector store to S3
    upload_vectorstore

    # Seed FGA tuples (optional)
    read -p "Seed FGA tuples? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        seed_fga_tuples
    fi

    # Print summary
    print_summary
}

main
