#!/usr/bin/env python3
"""
Seed FGA stored tuples for:
  1. User subscription assignments (user -> subscriber -> subscription_tier)
  2. Content-to-tag mappings (content_tag -> tagged -> content)

Usage:
  # Seed a user's subscription by email (requires AUTH0_DOMAIN, AUTH0_MGMT_CLIENT_ID, AUTH0_MGMT_CLIENT_SECRET):
  python seed-fga-subscriptions.py --email gut.health@atko.email --tier premium

  # Seed content tuples only:
  python seed-fga-subscriptions.py --content-only

  # Seed both:
  python seed-fga-subscriptions.py --email gut.health@atko.email --tier premium --content

Environment variables required:
  FGA_STORE_ID         - Auth0 FGA Store ID
  FGA_CLIENT_ID        - FGA M2M Client ID
  FGA_CLIENT_SECRET    - FGA M2M Client Secret
  FGA_API_URL          - FGA API base URL (default: https://api.us1.fga.dev)
  FGA_MODEL_ID         - Authorization model ID (optional)

  # For user lookup by email (optional - can pass --user-id directly):
  AUTH0_DOMAIN         - Auth0 tenant domain
  AUTH0_MGMT_CLIENT_ID     - Management API M2M client ID
  AUTH0_MGMT_CLIENT_SECRET - Management API M2M client secret
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

FGA_API_URL = os.environ.get("FGA_API_URL", "https://api.us1.fga.dev")
FGA_STORE_ID = os.environ.get("FGA_STORE_ID", "01KPJV2HY6Q7SC8HR65NNH8V9E")
FGA_MODEL_ID = os.environ.get("FGA_MODEL_ID", "")
FGA_CLIENT_ID = os.environ.get("FGA_CLIENT_ID", "Qc9YEM70sn6Fkcu9hBgXq0q4KElk9FRR")
FGA_CLIENT_SECRET = os.environ.get("FGA_CLIENT_SECRET", "Uzq7vTQf80OCV0rg0-UNs4dAmEM3dv7NL-K1Hi7tv8RCEYqifsyI7Wb50rzDpxqy")
FGA_TOKEN_URL = "https://auth.fga.dev/oauth/token"
FGA_API_AUDIENCE = "https://api.us1.fga.dev/"

AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "violet-hookworm-18506.cic-demo-platform.auth0app.com")
AUTH0_MGMT_CLIENT_ID = os.environ.get("AUTH0_MGMT_CLIENT_ID", "")
AUTH0_MGMT_CLIENT_SECRET = os.environ.get("AUTH0_MGMT_CLIENT_SECRET", "")

SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
CONTENT_TUPLES_FILE = PROJECT_ROOT / "infrastructure" / "fga" / "tuples" / "content-tuples.json"


def get_fga_token() -> str:
    """Get FGA access token via client credentials."""
    resp = requests.post(
        FGA_TOKEN_URL,
        json={
            "client_id": FGA_CLIENT_ID,
            "client_secret": FGA_CLIENT_SECRET,
            "audience": FGA_API_AUDIENCE,
            "grant_type": "client_credentials",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_auth0_mgmt_token() -> str:
    """Get Auth0 Management API token."""
    resp = requests.post(
        f"https://{AUTH0_DOMAIN}/oauth/token",
        json={
            "client_id": AUTH0_MGMT_CLIENT_ID,
            "client_secret": AUTH0_MGMT_CLIENT_SECRET,
            "audience": f"https://{AUTH0_DOMAIN}/api/v2/",
            "grant_type": "client_credentials",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def lookup_user_id_by_email(email: str) -> str:
    """Look up Auth0 user_id by email via Management API."""
    if not AUTH0_MGMT_CLIENT_ID or not AUTH0_MGMT_CLIENT_SECRET:
        raise ValueError(
            "AUTH0_MGMT_CLIENT_ID and AUTH0_MGMT_CLIENT_SECRET required for email lookup.\n"
            "Alternatively, pass --user-id directly."
        )

    token = get_auth0_mgmt_token()
    resp = requests.get(
        f"https://{AUTH0_DOMAIN}/api/v2/users-by-email",
        params={"email": email},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    users = resp.json()
    if not users:
        raise ValueError(f"No Auth0 user found for email: {email}")
    user_id = users[0]["user_id"]
    print(f"Found Auth0 user_id: {user_id}")
    return user_id


def write_fga_tuples(token: str, tuples: list) -> None:
    """Write tuples to FGA store in batches of 100."""
    url = f"{FGA_API_URL}/stores/{FGA_STORE_ID}/write"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    batch_size = 100
    for i in range(0, len(tuples), batch_size):
        batch = tuples[i:i + batch_size]
        body = {"writes": {"tuple_keys": batch}}
        if FGA_MODEL_ID:
            body["authorization_model_id"] = FGA_MODEL_ID

        resp = requests.post(url, json=body, headers=headers, timeout=15)
        if resp.status_code == 200:
            print(f"  Wrote {len(batch)} tuples (batch {i // batch_size + 1})")
        else:
            err = resp.json()
            # Ignore already-exists errors
            if "already exists" in str(err).lower() or resp.status_code == 400:
                print(f"  Batch {i // batch_size + 1}: some tuples already exist (OK)")
            else:
                print(f"  ERROR writing tuples: {resp.status_code} - {err}")
                resp.raise_for_status()


def seed_user_subscription(user_id: str, tier: str) -> None:
    """Write user subscription tuple to FGA."""
    print(f"\nSeeding subscription: user:{user_id} -> subscriber -> subscription_tier:{tier}")
    token = get_fga_token()
    tuples = [
        {
            "user": f"user:{user_id}",
            "relation": "subscriber",
            "object": f"subscription_tier:{tier}",
        }
    ]
    write_fga_tuples(token, tuples)
    print(f"  Done: user:{user_id} is now a {tier} subscriber in FGA")


def seed_content_tuples() -> None:
    """Write all content-to-tag tuples to FGA."""
    print(f"\nSeeding content tuples from {CONTENT_TUPLES_FILE}")
    with open(CONTENT_TUPLES_FILE) as f:
        tuples = json.load(f)

    token = get_fga_token()
    write_fga_tuples(token, tuples)
    print(f"  Done: {len(tuples)} content tuples written")


def verify_subscription(user_id: str, tier: str) -> None:
    """Verify a user's subscription tuple exists in FGA."""
    print(f"\nVerifying: user:{user_id} subscriber subscription_tier:{tier}")
    token = get_fga_token()
    url = f"{FGA_API_URL}/stores/{FGA_STORE_ID}/check"
    body = {
        "tuple_key": {
            "user": f"user:{user_id}",
            "relation": "subscriber",
            "object": f"subscription_tier:{tier}",
        }
    }
    if FGA_MODEL_ID:
        body["authorization_model_id"] = FGA_MODEL_ID

    resp = requests.post(
        url,
        json=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()
    allowed = result.get("allowed", False)
    print(f"  Check result: {'ALLOWED ✓' if allowed else 'DENIED ✗'}")
    return allowed


def main():
    parser = argparse.ArgumentParser(description="Seed FGA subscription and content tuples")
    parser.add_argument("--email", help="User email to assign subscription to")
    parser.add_argument("--user-id", help="Auth0 user_id directly (e.g., auth0|xxx or google-oauth2|xxx)")
    parser.add_argument("--tier", default="premium", choices=["basic", "premium"], help="Subscription tier")
    parser.add_argument("--content", action="store_true", help="Also seed content tuples")
    parser.add_argument("--content-only", action="store_true", help="Only seed content tuples")
    parser.add_argument("--verify", action="store_true", help="Verify after seeding")
    args = parser.parse_args()

    if not FGA_STORE_ID:
        print("Error: FGA_STORE_ID is required")
        sys.exit(1)

    if args.content_only:
        seed_content_tuples()
        return

    if not args.email and not args.user_id:
        print("Error: --email or --user-id required (unless using --content-only)")
        parser.print_help()
        sys.exit(1)

    # Resolve user_id
    user_id = args.user_id
    if not user_id and args.email:
        user_id = lookup_user_id_by_email(args.email)

    # Seed subscription
    seed_user_subscription(user_id, args.tier)

    # Optionally seed content tuples
    if args.content:
        seed_content_tuples()

    # Optionally verify
    if args.verify:
        verify_subscription(user_id, args.tier)

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
