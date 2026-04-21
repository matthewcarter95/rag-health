#!/bin/bash
#
# Seed FGA Tuples for RAG Health ABAC Model
#
# This script loads the tag-based tuples that define which subscription tiers
# and roles can access which content tags. Content permissions are resolved
# dynamically through:
#
#   1. These tag tuples (loaded once):
#      subscription_tier:X#subscriber -> viewer -> content_tag:Y
#      role:X#member -> viewer -> content_tag:Y
#
#   2. Contextual tuples (passed at check time from JWT):
#      user:<id> is subscriber of subscription_tier:<tier>
#      user:<id> is member of role:<role>
#      content_tag:<tag> is tagged on content:<id>
#
# Prerequisites:
#   - FGA CLI installed (https://openfga.dev/docs/getting-started/install-sdk)
#   - FGA_STORE_ID environment variable set
#   - FGA_API_URL environment variable set (if not using default)
#
# Usage: ./seed-fga-tuples.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TUPLES_DIR="${PROJECT_ROOT}/infrastructure/fga/tuples"

# Check for required environment variables
if [ -z "$FGA_STORE_ID" ]; then
    echo "Error: FGA_STORE_ID environment variable is required"
    echo "Set it with: export FGA_STORE_ID=<your-store-id>"
    exit 1
fi

# Check if fga CLI is installed
if ! command -v fga &> /dev/null; then
    echo "Error: FGA CLI not found."
    echo "Install with: brew install openfga/tap/fga"
    exit 1
fi

echo "=== RAG Health FGA ABAC Tuple Seeding ==="
echo ""
echo "Store ID: ${FGA_STORE_ID}"
echo ""

# Write tag tuples (subscription_tier/role -> content_tag permissions)
echo "Loading ABAC tag tuples..."
echo "These define: subscription_tier/role -> viewer -> content_tag"
echo ""
fga tuple write \
    --store-id "${FGA_STORE_ID}" \
    --file "${TUPLES_DIR}/tag-tuples.json"

echo ""
echo "Tag tuples loaded successfully!"
echo ""

# Verify tag tuples
echo "=== Verifying Tag Tuples ==="
echo ""
echo "Checking content_tag:basic viewers:"
fga tuple read --store-id "${FGA_STORE_ID}" --object "content_tag:basic" 2>/dev/null || echo "(error reading tuples)"
echo ""

echo "Checking content_tag:premium viewers:"
fga tuple read --store-id "${FGA_STORE_ID}" --object "content_tag:premium" 2>/dev/null || echo "(error reading tuples)"
echo ""

echo "Checking content_tag:clinical viewers:"
fga tuple read --store-id "${FGA_STORE_ID}" --object "content_tag:clinical" 2>/dev/null || echo "(error reading tuples)"
echo ""

echo "=== ABAC Test Commands ==="
echo ""
echo "To test ABAC authorization with contextual tuples, use:"
echo ""
echo "# Basic user checking basic content:"
echo "fga check --store-id ${FGA_STORE_ID} \\"
echo "  'user:test-user' viewer content:test-content \\"
echo "  --contextual-tuple 'user:test-user subscriber subscription_tier:basic' \\"
echo "  --contextual-tuple 'content_tag:basic tagged content:test-content'"
echo ""
echo "# Premium user checking premium content:"
echo "fga check --store-id ${FGA_STORE_ID} \\"
echo "  'user:test-user' viewer content:test-content \\"
echo "  --contextual-tuple 'user:test-user subscriber subscription_tier:premium' \\"
echo "  --contextual-tuple 'content_tag:premium tagged content:test-content'"
echo ""
echo "# Healthcare provider checking clinical content:"
echo "fga check --store-id ${FGA_STORE_ID} \\"
echo "  'user:test-provider' viewer content:clinical-doc \\"
echo "  --contextual-tuple 'user:test-provider member role:healthcare_provider' \\"
echo "  --contextual-tuple 'content_tag:clinical tagged content:clinical-doc'"
echo ""
echo "=== Done ==="
