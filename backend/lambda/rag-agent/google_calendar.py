"""
Google Calendar Integration Module

Integrates with Google Calendar via Auth0 Management API or Token Exchange.
Retrieves Google access tokens from linked/connected accounts.
"""

import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

# Configuration
AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "violet-hookworm-18506.cic-demo-platform.auth0app.com")
AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID", "")
AUTH0_CLIENT_SECRET = os.environ.get("AUTH0_CLIENT_SECRET", "")
AUTH0_M2M_CLIENT_ID = os.environ.get("AUTH0_M2M_CLIENT_ID", "")
AUTH0_M2M_CLIENT_SECRET = os.environ.get("AUTH0_M2M_CLIENT_SECRET", "")
MYACCOUNT_BASE_URL = f"https://{AUTH0_DOMAIN}/me/v1"
AUTH0_TOKEN_URL = f"https://{AUTH0_DOMAIN}/oauth/token"
AUTH0_MGMT_API = f"https://{AUTH0_DOMAIN}/api/v2"

GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"

# Cache for M2M token
_m2m_token_cache: Dict[str, Any] = {}


class CalendarError(Exception):
    """Calendar operation error."""

    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def get_m2m_token() -> Optional[str]:
    """
    Get an M2M access token for the Auth0 Management API.

    Returns:
        Management API access token or None
    """
    global _m2m_token_cache

    # Check cache
    if _m2m_token_cache.get("token") and _m2m_token_cache.get("expires_at", 0) > datetime.utcnow().timestamp():
        return _m2m_token_cache["token"]

    if not AUTH0_M2M_CLIENT_ID or not AUTH0_M2M_CLIENT_SECRET:
        print("[Calendar] Missing M2M client credentials")
        return None

    try:
        response = requests.post(
            AUTH0_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": AUTH0_M2M_CLIENT_ID,
                "client_secret": AUTH0_M2M_CLIENT_SECRET,
                "audience": AUTH0_MGMT_API,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            _m2m_token_cache["token"] = data.get("access_token")
            _m2m_token_cache["expires_at"] = datetime.utcnow().timestamp() + data.get("expires_in", 3600) - 60
            return _m2m_token_cache["token"]

        print(f"[Calendar] Failed to get M2M token: {response.status_code} - {response.text}")
        return None

    except Exception as e:
        print(f"[Calendar] Error getting M2M token: {e}")
        return None


def check_google_connected(user_access_token: str) -> bool:
    """
    Check if user has connected their Google account via Auth0 Connected Accounts.

    Args:
        user_access_token: User's Auth0 access token (MyAccount audience)

    Returns:
        True if Google is connected, False otherwise
    """
    try:
        url = f"{MYACCOUNT_BASE_URL}/connected-accounts/accounts"
        print(f"[Calendar] Checking connected accounts at: {url}")

        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {user_access_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )

        if response.status_code != 200:
            print(f"[Calendar] Connected accounts check failed: {response.status_code}")
            return False

        data = response.json()
        accounts = data.get("accounts", data) if isinstance(data, dict) else data

        for acc in accounts:
            conn = acc.get("connection", "").lower()
            if "google" in conn:
                print(f"[Calendar] Found Google connected account: {acc.get('id')}")
                return True

        print("[Calendar] No Google account found in connected accounts")
        return False

    except Exception as e:
        print(f"[Calendar] Error checking connected accounts: {e}")
        return False


def get_google_token_via_management_api(user_id: str) -> Optional[str]:
    """
    Get Google access token via Auth0 Management API.

    Uses the Management API to fetch the user's identities and extract
    the Google identity provider access token.

    Args:
        user_id: Auth0 user ID (sub claim from JWT)

    Returns:
        Google OAuth access token or None

    Raises:
        CalendarError: If API call fails
    """
    m2m_token = get_m2m_token()
    if not m2m_token:
        print("[Calendar] Cannot get M2M token for Management API")
        raise CalendarError(
            "Server configuration error: M2M credentials not configured for Management API",
            500
        )

    try:
        # URL encode the user_id (it contains |)
        import urllib.parse
        encoded_user_id = urllib.parse.quote(user_id, safe='')

        url = f"{AUTH0_MGMT_API}/users/{encoded_user_id}"
        print(f"[Calendar] Fetching user from Management API: {url}")

        response = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {m2m_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )

        print(f"[Calendar] Management API response status: {response.status_code}")

        if response.status_code != 200:
            print(f"[Calendar] Management API error: {response.text}")
            raise CalendarError(f"Failed to fetch user: {response.status_code}", response.status_code)

        user_data = response.json()
        identities = user_data.get("identities", [])

        print(f"[Calendar] User has {len(identities)} identities")

        # Find Google identity
        for identity in identities:
            provider = identity.get("provider", "")
            if "google" in provider.lower():
                access_token = identity.get("access_token")
                if access_token:
                    print("[Calendar] Found Google access token in user identity")
                    return access_token
                print("[Calendar] Google identity found but no access_token")

        print("[Calendar] No Google identity with access_token found")
        return None

    except requests.RequestException as e:
        print(f"[Calendar] Management API request exception: {str(e)}")
        raise CalendarError(f"Failed to fetch user identities: {str(e)}")


def get_google_token_via_token_exchange(user_refresh_token: str) -> Optional[str]:
    """
    Get Google access token via Auth0 Federated Connection Token Exchange.

    Uses the federated-connection-access-token grant to exchange a user's
    refresh token for a Google access token.

    Args:
        user_refresh_token: User's Auth0 refresh token

    Returns:
        Google OAuth access token or None if exchange fails

    Raises:
        CalendarError: If token exchange fails
    """
    if not AUTH0_CLIENT_ID or not AUTH0_CLIENT_SECRET:
        print("[Calendar] Missing AUTH0_CLIENT_ID or AUTH0_CLIENT_SECRET")
        raise CalendarError(
            "Server configuration error: Auth0 client credentials not configured",
            500
        )

    try:
        print(f"[Calendar] Performing token exchange for Google access token")

        payload = {
            "grant_type": "urn:auth0:params:oauth:grant-type:token-exchange:federated-connection-access-token",
            "subject_token": user_refresh_token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:refresh_token",
            "requested_token_type": "http://auth0.com/oauth/token-type/federated-connection-access-token",
            "connection": "google-oauth2",
            "client_id": AUTH0_CLIENT_ID,
            "client_secret": AUTH0_CLIENT_SECRET,
        }

        response = requests.post(
            AUTH0_TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )

        print(f"[Calendar] Token exchange response status: {response.status_code}")

        if response.status_code == 200:
            token_data = response.json()
            access_token = token_data.get("access_token")
            if access_token:
                print("[Calendar] Successfully obtained Google access token via token exchange")
                return access_token
            print("[Calendar] Token exchange response missing access_token")
            return None

        error_data = response.json() if response.text else {}
        error_msg = error_data.get("error_description", error_data.get("error", response.text))
        print(f"[Calendar] Token exchange failed: {error_msg}")

        if response.status_code == 400:
            if "invalid_grant" in str(error_data.get("error", "")):
                return None  # User needs to re-connect Google
            raise CalendarError(f"Token exchange error: {error_msg}", 400)

        raise CalendarError(f"Token exchange failed: {error_msg}", response.status_code)

    except requests.RequestException as e:
        print(f"[Calendar] Token exchange request exception: {str(e)}")
        raise CalendarError(f"Failed to exchange token: {str(e)}")


def get_google_token_via_connected_accounts(user_access_token: str) -> Optional[str]:
    """
    Get Google access token via Auth0 Connected Accounts API.

    Uses the MyAccount Connected Accounts token endpoint to retrieve
    the stored Google access token.

    Args:
        user_access_token: User's Auth0 access token (MyAccount audience)

    Returns:
        Google OAuth access token or None if not available

    Raises:
        CalendarError: If API call fails
    """
    try:
        # First get the connected account ID for Google
        accounts_url = f"{MYACCOUNT_BASE_URL}/connected-accounts/accounts"
        print(f"[Calendar] Fetching connected accounts to find Google account ID")

        response = requests.get(
            accounts_url,
            headers={
                "Authorization": f"Bearer {user_access_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )

        if response.status_code != 200:
            print(f"[Calendar] Failed to list connected accounts: {response.status_code}")
            return None

        data = response.json()
        accounts = data.get("accounts", data) if isinstance(data, dict) else data

        google_account_id = None
        for acc in accounts:
            conn = acc.get("connection", "").lower()
            if "google" in conn:
                google_account_id = acc.get("id")
                print(f"[Calendar] Found Google account ID: {google_account_id}")
                break

        if not google_account_id:
            print("[Calendar] No Google connected account found")
            return None

        # Now get the token for this connected account
        token_url = f"{MYACCOUNT_BASE_URL}/connected-accounts/accounts/{google_account_id}/token"
        print(f"[Calendar] Fetching Google token from: {token_url}")

        token_response = requests.get(
            token_url,
            headers={
                "Authorization": f"Bearer {user_access_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )

        print(f"[Calendar] Token endpoint response status: {token_response.status_code}")

        if token_response.status_code == 200:
            token_data = token_response.json()
            access_token = token_data.get("access_token")
            if access_token:
                print("[Calendar] Successfully retrieved Google access token via Connected Accounts")
                return access_token
            print(f"[Calendar] Token response missing access_token: {token_data}")
            return None

        print(f"[Calendar] Token endpoint error: {token_response.text}")
        return None

    except Exception as e:
        print(f"[Calendar] Error retrieving token via Connected Accounts: {e}")
        return None


def get_google_token_from_myaccount(
    user_access_token: str,
    user_refresh_token: str = "",
    user_id: str = ""
) -> Optional[str]:
    """
    Get Google OAuth token for calendar access.

    Tries multiple methods in order:
    1. Connected Accounts API (using MyAccount token)
    2. Management API (if M2M credentials configured and user_id provided)
    3. Token exchange (if refresh token provided)

    Args:
        user_access_token: User's Auth0 access token (MyAccount audience)
        user_refresh_token: User's Auth0 refresh token (for token exchange)
        user_id: Auth0 user ID (for Management API)

    Returns:
        Google OAuth access token or None if not connected

    Raises:
        CalendarError: If API call fails
    """
    # First check if Google is connected
    if not check_google_connected(user_access_token):
        return None

    # Method 1: Try Connected Accounts API (preferred for CIC tenants)
    try:
        print("[Calendar] Attempting to get Google token via Connected Accounts API")
        token = get_google_token_via_connected_accounts(user_access_token)
        if token:
            return token
        print("[Calendar] Connected Accounts API didn't return a token, trying other methods")
    except Exception as e:
        print(f"[Calendar] Connected Accounts API failed: {e}, trying other methods")

    # Method 2: Try Management API if we have user_id and M2M credentials
    if user_id and AUTH0_M2M_CLIENT_ID and AUTH0_M2M_CLIENT_SECRET:
        try:
            print("[Calendar] Attempting to get Google token via Management API")
            token = get_google_token_via_management_api(user_id)
            if token:
                return token
            print("[Calendar] Management API didn't return a token, trying other methods")
        except CalendarError as e:
            print(f"[Calendar] Management API failed: {e.message}, trying other methods")

    # Method 3: Try token exchange if we have a refresh token
    if user_refresh_token and AUTH0_CLIENT_ID and AUTH0_CLIENT_SECRET:
        try:
            print("[Calendar] Attempting to get Google token via token exchange")
            token = get_google_token_via_token_exchange(user_refresh_token)
            if token:
                return token
        except CalendarError as e:
            print(f"[Calendar] Token exchange failed: {e.message}")
            raise

    # No method available
    print("[Calendar] No method available to retrieve Google token")
    raise CalendarError(
        "Cannot retrieve Google Calendar access token. "
        "Please ensure your Google account is connected and try again.",
        500
    )


def list_calendar_events(
    google_token: str,
    calendar_id: str = "primary",
    max_results: int = 10,
    time_min: Optional[datetime] = None,
    time_max: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    List events from user's Google Calendar.

    Args:
        google_token: Google OAuth access token
        calendar_id: Calendar ID (default: "primary" for user's main calendar)
        max_results: Maximum number of events to return
        time_min: Start of time range (default: now)
        time_max: End of time range (default: 30 days from now)

    Returns:
        List of calendar event dictionaries

    Raises:
        CalendarError: If API call fails
    """
    if time_min is None:
        time_min = datetime.utcnow()
    if time_max is None:
        time_max = time_min + timedelta(days=30)

    params = {
        "maxResults": max_results,
        "timeMin": time_min.isoformat() + "Z",
        "timeMax": time_max.isoformat() + "Z",
        "singleEvents": True,
        "orderBy": "startTime",
    }

    try:
        response = requests.get(
            f"{GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}/events",
            headers={
                "Authorization": f"Bearer {google_token}",
                "Content-Type": "application/json",
            },
            params=params,
            timeout=10,
        )

        if response.status_code == 401:
            raise CalendarError("Google token expired or invalid", 401)

        if response.status_code != 200:
            raise CalendarError(
                f"Google Calendar API error: {response.status_code} - {response.text}",
                response.status_code
            )

        return response.json().get("items", [])

    except requests.RequestException as e:
        raise CalendarError(f"Failed to fetch calendar events: {str(e)}")


def create_calendar_event(
    google_token: str,
    summary: str,
    start_time: datetime,
    end_time: datetime,
    description: Optional[str] = None,
    location: Optional[str] = None,
    calendar_id: str = "primary",
    attendees: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Create an event on user's Google Calendar.

    Args:
        google_token: Google OAuth access token
        summary: Event title
        start_time: Event start datetime
        end_time: Event end datetime
        description: Optional event description
        location: Optional event location
        calendar_id: Calendar ID (default: "primary")
        attendees: Optional list of attendee email addresses

    Returns:
        Created event dictionary

    Raises:
        CalendarError: If API call fails
    """
    event_body = {
        "summary": summary,
        "start": {
            "dateTime": start_time.isoformat(),
            "timeZone": "UTC",
        },
        "end": {
            "dateTime": end_time.isoformat(),
            "timeZone": "UTC",
        },
    }

    if description:
        event_body["description"] = description

    if location:
        event_body["location"] = location

    if attendees:
        event_body["attendees"] = [{"email": email} for email in attendees]

    try:
        response = requests.post(
            f"{GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}/events",
            headers={
                "Authorization": f"Bearer {google_token}",
                "Content-Type": "application/json",
            },
            json=event_body,
            timeout=10,
        )

        if response.status_code == 401:
            raise CalendarError("Google token expired or invalid", 401)

        if response.status_code not in (200, 201):
            raise CalendarError(
                f"Failed to create event: {response.status_code} - {response.text}",
                response.status_code
            )

        return response.json()

    except requests.RequestException as e:
        raise CalendarError(f"Failed to create calendar event: {str(e)}")


def format_events_for_display(events: List[Dict[str, Any]]) -> str:
    """
    Format calendar events for human-readable display.

    Args:
        events: List of Google Calendar event dictionaries

    Returns:
        Formatted string representation of events
    """
    if not events:
        return "No upcoming events found."

    lines = ["📅 Upcoming Calendar Events:\n"]

    for event in events:
        summary = event.get("summary", "Untitled Event")
        start = event.get("start", {})

        # Handle all-day events vs timed events
        if "dateTime" in start:
            start_dt = datetime.fromisoformat(start["dateTime"].replace("Z", "+00:00"))
            time_str = start_dt.strftime("%B %d, %Y at %I:%M %p")
        elif "date" in start:
            time_str = f"{start['date']} (all day)"
        else:
            time_str = "Time not specified"

        location = event.get("location", "")
        location_str = f" | 📍 {location}" if location else ""

        lines.append(f"• {summary}")
        lines.append(f"  🕐 {time_str}{location_str}")
        lines.append("")

    return "\n".join(lines)


# LangChain Tool Functions

def list_events_tool(
    user_access_token: str,
    user_refresh_token: str = "",
    user_id: str = ""
) -> str:
    """
    LangChain tool function to list calendar events.

    Args:
        user_access_token: User's Auth0 access token (MyAccount audience)
        user_refresh_token: User's Auth0 refresh token (for token exchange)
        user_id: Auth0 user ID (for Management API)

    Returns:
        Formatted string of upcoming events or error message
    """
    try:
        google_token = get_google_token_from_myaccount(
            user_access_token,
            user_refresh_token,
            user_id
        )

        if google_token is None:
            return (
                "Google Calendar is not connected. You have authorized calendar access, but "
                "you still need to link your Google account through Auth0 Connected Accounts. "
                "This requires initiating a connection request via the MyAccount API."
            )

        events = list_calendar_events(google_token)
        return format_events_for_display(events)

    except CalendarError as e:
        return f"Calendar error: {e.message}"
    except Exception as e:
        return f"Failed to retrieve calendar events: {str(e)}"


def create_event_tool(
    user_access_token: str,
    user_refresh_token: str,
    summary: str,
    start_time: str,
    end_time: str,
    description: Optional[str] = None,
    user_id: str = "",
) -> str:
    """
    LangChain tool function to create a calendar event.

    Args:
        user_access_token: User's Auth0 access token (MyAccount audience)
        user_refresh_token: User's Auth0 refresh token (for token exchange)
        summary: Event title
        start_time: ISO format datetime string
        end_time: ISO format datetime string
        description: Optional event description
        user_id: Auth0 user ID (for Management API)

    Returns:
        Confirmation message or error
    """
    try:
        google_token = get_google_token_from_myaccount(
            user_access_token,
            user_refresh_token,
            user_id
        )

        if google_token is None:
            return (
                "Google Calendar is not connected. Please connect your Google account "
                "in your profile settings to enable calendar features."
            )

        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))

        event = create_calendar_event(
            google_token=google_token,
            summary=summary,
            start_time=start_dt,
            end_time=end_dt,
            description=description,
        )

        event_link = event.get("htmlLink", "")
        return f"✅ Event '{summary}' created successfully!\nView event: {event_link}"

    except CalendarError as e:
        return f"Calendar error: {e.message}"
    except ValueError as e:
        return f"Invalid date format: {str(e)}. Please use ISO format (YYYY-MM-DDTHH:MM:SS)"
    except Exception as e:
        return f"Failed to create calendar event: {str(e)}"
