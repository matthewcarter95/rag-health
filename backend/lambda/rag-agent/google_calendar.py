"""
Google Calendar Integration Module

Integrates with Google Calendar via Auth0 Token Vault.
Uses auth0-ai-langchain SDK for simplified token exchange.
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
AUTH0_TOKEN_URL = f"https://{AUTH0_DOMAIN}/oauth/token"

GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"

# Google Calendar scopes needed
GOOGLE_CALENDAR_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/calendar.freebusy",
    "https://www.googleapis.com/auth/calendar.events",
]


class CalendarError(Exception):
    """Calendar operation error."""

    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def get_google_token_via_token_vault(refresh_token: str) -> Optional[str]:
    """
    Get Google access token using Auth0 Token Vault (federated connection token exchange).

    This is the simplified approach recommended by Auth0 AI SDK.
    Exchanges a user's Auth0 refresh token for a Google access token.

    Args:
        refresh_token: User's Auth0 refresh token

    Returns:
        Google OAuth access token or None if exchange fails

    Raises:
        CalendarError: If token exchange fails
    """
    if not refresh_token:
        print("[Calendar] No refresh token provided")
        return None

    if not AUTH0_CLIENT_ID or not AUTH0_CLIENT_SECRET:
        print("[Calendar] Missing AUTH0_CLIENT_ID or AUTH0_CLIENT_SECRET")
        raise CalendarError(
            "Server configuration error: Auth0 client credentials not configured",
            500
        )

    try:
        print(f"[Calendar] Performing Token Vault exchange for Google access token")

        # Token Vault uses the federated-connection-access-token grant type
        payload = {
            "grant_type": "urn:auth0:params:oauth:grant-type:token-exchange:federated-connection-access-token",
            "subject_token": refresh_token,
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

        print(f"[Calendar] Token Vault exchange response status: {response.status_code}")

        if response.status_code == 200:
            token_data = response.json()
            access_token = token_data.get("access_token")
            if access_token:
                print("[Calendar] Successfully obtained Google access token via Token Vault")
                return access_token
            print("[Calendar] Token exchange response missing access_token")
            return None

        error_data = response.json() if response.text else {}
        error_code = error_data.get("error", "")
        error_msg = error_data.get("error_description", response.text)
        print(f"[Calendar] Token Vault exchange failed: {error_code} - {error_msg}")

        # Handle specific error cases
        if error_code == "invalid_grant":
            # User needs to re-authenticate or re-connect Google
            raise CalendarError(
                "Your Google account connection has expired. Please reconnect your Google account.",
                401
            )
        elif error_code == "access_denied":
            raise CalendarError(
                "Access denied. Please ensure your Google account is connected with calendar permissions.",
                403
            )
        elif "Service not enabled" in error_msg:
            raise CalendarError(
                "Token Vault service is not enabled. Contact administrator.",
                503
            )

        raise CalendarError(f"Token exchange failed: {error_msg}", response.status_code)

    except requests.RequestException as e:
        print(f"[Calendar] Token exchange request exception: {str(e)}")
        raise CalendarError(f"Failed to exchange token: {str(e)}")


def check_google_connected(refresh_token: str) -> bool:
    """
    Check if user can access Google via Token Vault.

    Attempts a token exchange to verify the Google connection is valid.

    Args:
        refresh_token: User's Auth0 refresh token

    Returns:
        True if Google token can be obtained, False otherwise
    """
    if not refresh_token:
        return False

    try:
        token = get_google_token_via_token_vault(refresh_token)
        return token is not None
    except CalendarError:
        return False


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

    lines = ["Upcoming Calendar Events:\n"]

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
        location_str = f" | Location: {location}" if location else ""

        lines.append(f"* {summary}")
        lines.append(f"  Time: {time_str}{location_str}")
        lines.append("")

    return "\n".join(lines)


# LangChain Tool Functions (using Token Vault)

def list_events_tool(refresh_token: str) -> str:
    """
    LangChain tool function to list calendar events.

    Uses Auth0 Token Vault to exchange refresh token for Google access token.

    Args:
        refresh_token: User's Auth0 refresh token

    Returns:
        Formatted string of upcoming events or error message
    """
    try:
        if not refresh_token:
            return (
                "Google Calendar is not connected. To enable calendar features, "
                "please ensure you're logged in with Google and have granted calendar permissions."
            )

        google_token = get_google_token_via_token_vault(refresh_token)

        if google_token is None:
            return (
                "Unable to access your Google Calendar. Please reconnect your Google account "
                "and ensure calendar permissions are granted."
            )

        events = list_calendar_events(google_token)
        return format_events_for_display(events)

    except CalendarError as e:
        return f"Calendar error: {e.message}"
    except Exception as e:
        return f"Failed to retrieve calendar events: {str(e)}"


def create_event_tool(
    refresh_token: str,
    summary: str,
    start_time: str,
    end_time: str,
    description: Optional[str] = None,
) -> str:
    """
    LangChain tool function to create a calendar event.

    Uses Auth0 Token Vault to exchange refresh token for Google access token.

    Args:
        refresh_token: User's Auth0 refresh token
        summary: Event title
        start_time: ISO format datetime string
        end_time: ISO format datetime string
        description: Optional event description

    Returns:
        Confirmation message or error
    """
    try:
        if not refresh_token:
            return (
                "Google Calendar is not connected. Please ensure you're logged in with Google "
                "and have granted calendar permissions to create events."
            )

        google_token = get_google_token_via_token_vault(refresh_token)

        if google_token is None:
            return (
                "Unable to access your Google Calendar. Please reconnect your Google account "
                "and ensure calendar write permissions are granted."
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
        return f"Event '{summary}' created successfully!\nView event: {event_link}"

    except CalendarError as e:
        return f"Calendar error: {e.message}"
    except ValueError as e:
        return f"Invalid date format: {str(e)}. Please use ISO format (YYYY-MM-DDTHH:MM:SS)"
    except Exception as e:
        return f"Failed to create calendar event: {str(e)}"
