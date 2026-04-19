#!/bin/bash
#
# Create Auth0 API/Resource Server for RAG Health
#
# Prerequisites:
#   - Auth0 CLI installed and configured (auth0 login)
#   - Management API access
#
# Usage: ./create-auth0-api.sh

set -e

# Configuration
API_NAME="RAG Health API"
API_IDENTIFIER="https://api.rag-health.example.com"

echo "Creating Auth0 API: ${API_NAME}"
echo "Identifier: ${API_IDENTIFIER}"
echo ""

# Check if auth0 CLI is installed
if ! command -v auth0 &> /dev/null; then
    echo "Error: Auth0 CLI not found. Install with: brew install auth0/auth0-cli/auth0"
    exit 1
fi

# Create the API
auth0 apis create \
    --name "${API_NAME}" \
    --identifier "${API_IDENTIFIER}" \
    --signing-alg "RS256" \
    --scopes "read:content:Query gut health content (FGA determines which specific content)" \
    --scopes "read:calendar:Read events from user's Google Calendar" \
    --scopes "write:calendar:Create events on user's Google Calendar" \
    --scopes "read:profile:Read user profile and subscription info"

echo ""
echo "✅ API created successfully!"
echo ""
echo "Next steps:"
echo "1. Configure your application to request tokens with audience: ${API_IDENTIFIER}"
echo "2. Add the Auth0 Action to enrich tokens with custom claims"
echo "3. Deploy FGA tuples for content authorization"
echo ""
echo "Example token request scopes: openid profile read:content read:calendar"
