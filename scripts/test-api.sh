#!/bin/bash
#
# Test RAG Health API
#
# Usage: ./test-api.sh [query]
# Example: ./test-api.sh "What are probiotics?"

API_URL="https://g2bj72k2hebslj5ejdlh7htoyy0ivbay.lambda-url.us-east-1.on.aws"
AUTH0_DOMAIN="violet-hookworm-18506.cic-demo-platform.auth0app.com"
CLIENT_ID="hNVKiCUshgKp4a7caNaddRHZoRQaa8fa"
CLIENT_SECRET="JGYvuJ-iC15VMontKyj5rfeGe-G48FUOIuVF-ZtlEtBoI1WVev9WfCCNMknOnj-o"
AUDIENCE="https://api.rag-health.example.com"

# Default query
QUERY="${1:-What is the gut microbiome?}"

echo "Getting Auth0 token..."
TOKEN=$(curl -s --request POST \
  --url "https://${AUTH0_DOMAIN}/oauth/token" \
  --header "content-type: application/json" \
  --data "{
    \"client_id\": \"${CLIENT_ID}\",
    \"client_secret\": \"${CLIENT_SECRET}\",
    \"audience\": \"${AUDIENCE}\",
    \"grant_type\": \"client_credentials\"
  }" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

if [ -z "$TOKEN" ]; then
  echo "Failed to get token"
  exit 1
fi

echo ""
echo "Query: $QUERY"
echo "---"
echo ""

curl -s "${API_URL}/query" \
  -X POST \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"$QUERY\"}" | python3 -c "
import sys, json
data = json.load(sys.stdin)
if 'error' in data:
    print(f\"Error: {data['error']}\")
else:
    print(f\"Answer:\\n{data.get('answer', 'No answer')}\\n\")
    print(f\"User Tier: {data.get('user_tier', 'unknown')}\")
"
