#!/bin/bash
#
# Seed FGA Tuples for RAG Health Content Authorization
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

echo "Seeding FGA tuples to store: ${FGA_STORE_ID}"
echo ""

# Write content access tuples
echo "Writing content access tuples..."
fga tuple write \
    --store-id "${FGA_STORE_ID}" \
    --file "${TUPLES_DIR}/content-tuples.json"

echo ""

# Write user subscription/role tuples
echo "Writing user subscription and role tuples..."
fga tuple write \
    --store-id "${FGA_STORE_ID}" \
    --file "${TUPLES_DIR}/user-tuples.json"

echo ""
echo "✅ FGA tuples seeded successfully!"
echo ""

# Verify tuples were written
echo "Verifying tuples..."
echo ""
echo "Sample content tuples:"
fga tuple read --store-id "${FGA_STORE_ID}" --object "content:microbiome-basics-001" 2>/dev/null || echo "(run 'fga tuple read' to verify)"

echo ""
echo "Sample user tuples:"
fga tuple read --store-id "${FGA_STORE_ID}" --user "user:auth0|basic-user-001" 2>/dev/null || echo "(run 'fga tuple read' to verify)"

echo ""
echo "Test authorization:"
echo "  fga check --store-id ${FGA_STORE_ID} 'user:auth0|basic-user-001' viewer content:microbiome-basics-001"
echo "  fga check --store-id ${FGA_STORE_ID} 'user:auth0|basic-user-001' viewer content:clinical-microbiome-001"
