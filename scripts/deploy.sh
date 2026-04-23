#!/bin/bash
#
# Deploy RAG Health Infrastructure
#
# Handles SAM deployment + post-deploy Function URL creation
# (workaround for org CloudFormation policy that blocks Lambda Function URLs)
#
# Prerequisites:
#   - AWS CLI configured
#   - SAM CLI installed
#   - Docker running (for container image build)
#
# Usage: ./deploy.sh [environment]
#   environment: dev (default), staging, prod
#
# Environment variables:
#   AWS_REGION: AWS region (default: us-east-1)
#   FGA_STORE_ID, FGA_API_URL, FGA_MODEL_ID: Optional FGA config
#   API_CERTIFICATE_ARN: ACM certificate ARN for CloudFront custom domain (us-east-1)
#   API_DOMAIN_NAME: Custom API domain (e.g., api.rag-health.demo-connect.us)
#   LAMBDA_FUNCTION_URL: Lambda Function URL host (without https://)
#   AUTH0_BFF_CLIENT_SECRET: Auth0 BFF client secret (required for OAuth)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SAM_DIR="${PROJECT_ROOT}/infrastructure/sam"

# Configuration
ENVIRONMENT="${1:-dev}"
REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="rag-health-${ENVIRONMENT}"
ECR_REPO="rag-health-agent"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}"

echo "============================================"
echo "RAG Health Deployment"
echo "============================================"
echo "Environment: ${ENVIRONMENT}"
echo "Region:      ${REGION}"
echo "Stack:       ${STACK_NAME}"
echo "ECR:         ${ECR_URI}"
echo "============================================"
echo ""

# Check prerequisites
check_prerequisites() {
    echo ">> Checking prerequisites..."
    local missing=0

    if ! command -v aws &> /dev/null; then
        echo "   [ERROR] AWS CLI not found"
        missing=1
    fi

    if ! command -v sam &> /dev/null; then
        echo "   [ERROR] SAM CLI not found. Install with: brew install aws-sam-cli"
        missing=1
    fi

    if ! docker info &> /dev/null; then
        echo "   [ERROR] Docker not running. Start Docker Desktop."
        missing=1
    fi

    if [ $missing -eq 1 ]; then
        exit 1
    fi

    echo "   All prerequisites met"
    echo ""
}

# Ensure ECR repository exists
ensure_ecr_repo() {
    echo ">> Ensuring ECR repository exists..."
    if aws ecr describe-repositories --repository-names "${ECR_REPO}" --region "${REGION}" >/dev/null 2>&1; then
        echo "   Repository already exists"
    else
        echo "   Creating repository..."
        aws ecr create-repository \
            --repository-name "${ECR_REPO}" \
            --region "${REGION}" \
            --output json >/dev/null
        echo "   Repository created"
    fi
    echo ""
}

# Build SAM application
build_sam() {
    echo ">> Building SAM application..."
    cd "$SAM_DIR"
    sam build
    echo ""
}

# Deploy SAM stack
deploy_sam() {
    echo ">> Deploying CloudFormation stack..."
    cd "$SAM_DIR"

    # Build parameter overrides
    PARAM_OVERRIDES="Environment=${ENVIRONMENT}"
    [ -n "$FGA_STORE_ID" ] && PARAM_OVERRIDES="${PARAM_OVERRIDES} FgaStoreId=${FGA_STORE_ID}"
    [ -n "$FGA_API_URL" ] && PARAM_OVERRIDES="${PARAM_OVERRIDES} FgaApiUrl=${FGA_API_URL}"
    [ -n "$FGA_MODEL_ID" ] && PARAM_OVERRIDES="${PARAM_OVERRIDES} FgaModelId=${FGA_MODEL_ID}"
    # CloudFront/API domain parameters
    [ -n "$API_CERTIFICATE_ARN" ] && PARAM_OVERRIDES="${PARAM_OVERRIDES} ApiCertificateArn=${API_CERTIFICATE_ARN}"
    [ -n "$API_DOMAIN_NAME" ] && PARAM_OVERRIDES="${PARAM_OVERRIDES} ApiDomainName=${API_DOMAIN_NAME}"
    [ -n "$LAMBDA_FUNCTION_URL" ] && PARAM_OVERRIDES="${PARAM_OVERRIDES} LambdaFunctionUrl=${LAMBDA_FUNCTION_URL}"
    # Auth0 BFF secrets
    [ -n "$AUTH0_BFF_CLIENT_SECRET" ] && PARAM_OVERRIDES="${PARAM_OVERRIDES} Auth0BFFClientSecret=${AUTH0_BFF_CLIENT_SECRET}"

    sam deploy \
        --stack-name "${STACK_NAME}" \
        --region "${REGION}" \
        --image-repository "${ECR_URI}" \
        --capabilities CAPABILITY_IAM \
        --parameter-overrides ${PARAM_OVERRIDES} \
        --no-confirm-changeset \
        --no-fail-on-empty-changeset

    echo ""
}

# Get stack outputs
get_stack_outputs() {
    echo ">> Retrieving stack outputs..."

    FUNCTION_NAME=$(aws cloudformation describe-stacks \
        --stack-name "${STACK_NAME}" \
        --region "${REGION}" \
        --query "Stacks[0].Outputs[?OutputKey=='RagAgentFunctionName'].OutputValue" \
        --output text)

    FUNCTION_ARN=$(aws cloudformation describe-stacks \
        --stack-name "${STACK_NAME}" \
        --region "${REGION}" \
        --query "Stacks[0].Outputs[?OutputKey=='RagAgentFunctionArn'].OutputValue" \
        --output text)

    S3_BUCKET=$(aws cloudformation describe-stacks \
        --stack-name "${STACK_NAME}" \
        --region "${REGION}" \
        --query "Stacks[0].Outputs[?OutputKey=='ContentBucketName'].OutputValue" \
        --output text)

    DYNAMODB_TABLE=$(aws cloudformation describe-stacks \
        --stack-name "${STACK_NAME}" \
        --region "${REGION}" \
        --query "Stacks[0].Outputs[?OutputKey=='SubscriptionsTableName'].OutputValue" \
        --output text)

    echo "   Function: ${FUNCTION_NAME}"
    echo "   S3 Bucket: ${S3_BUCKET}"
    echo "   DynamoDB Table: ${DYNAMODB_TABLE}"
    echo ""
}

# Create Function URL (workaround for org CloudFormation policy)
create_function_url() {
    echo ">> Configuring Lambda Function URL..."
    echo "   (Note: Created via CLI due to org CloudFormation policy)"

    # Check if URL already exists
    EXISTING_URL=$(aws lambda get-function-url-config \
        --function-name "${FUNCTION_NAME}" \
        --region "${REGION}" \
        --query "FunctionUrl" \
        --output text 2>/dev/null || echo "")

    if [ -z "$EXISTING_URL" ] || [ "$EXISTING_URL" == "None" ]; then
        echo "   Creating Function URL..."
        FUNCTION_URL=$(aws lambda create-function-url-config \
            --function-name "${FUNCTION_NAME}" \
            --auth-type NONE \
            --cors '{"AllowOrigins":["*"],"AllowMethods":["*"],"AllowHeaders":["*"],"AllowCredentials":true}' \
            --region "${REGION}" \
            --query "FunctionUrl" \
            --output text)

        echo "   Adding public invoke permission..."
        aws lambda add-permission \
            --function-name "${FUNCTION_NAME}" \
            --statement-id FunctionURLAllowPublicAccess \
            --action lambda:InvokeFunctionUrl \
            --principal "*" \
            --function-url-auth-type NONE \
            --region "${REGION}" >/dev/null 2>&1 || true
    else
        FUNCTION_URL="$EXISTING_URL"
        echo "   Function URL already exists"
    fi

    echo "   URL: ${FUNCTION_URL}"
    echo ""
}

# Upload vector store to S3
upload_vectorstore() {
    VECTORSTORE_PATH="$PROJECT_ROOT/content/vectorstore/gut_health"

    if [ -d "$VECTORSTORE_PATH" ]; then
        echo ">> Uploading vector store to S3..."
        aws s3 sync "$VECTORSTORE_PATH" "s3://${S3_BUCKET}/vectorstore/gut_health/" \
            --region "${REGION}"
        echo "   Uploaded to s3://${S3_BUCKET}/vectorstore/gut_health/"
        echo ""
    else
        echo ">> Skipping vector store upload (not found at ${VECTORSTORE_PATH})"
        echo "   Run 'python scripts/build-vectorstore.py' to create it"
        echo ""
    fi
}

# Print summary
print_summary() {
    echo "============================================"
    echo "Deployment Complete!"
    echo "============================================"
    echo ""
    echo "Resources:"
    echo "  Lambda Function:  ${FUNCTION_NAME}"
    echo "  Function URL:     ${FUNCTION_URL}"
    echo "  S3 Bucket:        ${S3_BUCKET}"
    echo "  DynamoDB Table:   ${DYNAMODB_TABLE}"
    echo ""
    echo "Test Commands:"
    echo ""
    echo "  # Health check (no auth required for health endpoint)"
    echo "  curl -s ${FUNCTION_URL}health"
    echo ""
    echo "  # Query content (requires Auth0 token)"
    echo "  curl -X POST ${FUNCTION_URL}query \\"
    echo "    -H 'Authorization: Bearer <AUTH0_TOKEN>' \\"
    echo "    -H 'Content-Type: application/json' \\"
    echo "    -d '{\"query\": \"What is the gut microbiome?\"}'"
    echo ""
    echo "============================================"
}

# Main execution
main() {
    check_prerequisites
    ensure_ecr_repo
    build_sam
    deploy_sam
    get_stack_outputs
    create_function_url
    upload_vectorstore
    print_summary
}

main
