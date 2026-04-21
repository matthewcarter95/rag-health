"""
Google Calendar Integration Module

Integrates with Google Calendar via Auth0 Connected Accounts API.
Uses the MyAccount token to retrieve Google access tokens.
"""

import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

# Configuration
AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "violet-hookworm-18506.cic-demo-platform.auth0app.com")
MYACCOUNT_BASE_URL = f"https://{AUTH0_DOMAIN}/me/v1"

GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


class CalendarError(Exception):
    """Calendar operation error."""

    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def get_google_token_via_connected_accounts(myaccount_token: str) -> Optional[str]:
    """
    Get Google access token via Auth0 Connected Accounts API.

    Args:
        myaccount_token: User's Auth0 MyAccount access token

    Returns:
        Google OAuth access token or None if not available

    Raises:
        CalendarError: If API call fails
    """
    if not myaccount_token:
        print("[Calendar] No MyAccount token provided")
        return None

    try:
        # First get the connected accounts to find the Google account ID
        accounts_url = f"{MYACCOUNT_BASE_URL}/connected-accounts/accounts"
        print(f"[Calendar] Fetching connected accounts from: {accounts_url}")

        accounts_response = requests.get(
            accounts_url,
            headers={
                "Authorization": f"Bearer {myaccount_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )

        print(f"[Calendar] Connected accounts response status: {accounts_response.status_code}")

        if accounts_response.status_code != 200:
            print(f"[Calendar] Failed to list connected accounts: {accounts_response.text}")
            return None

        data = accounts_response.json()
        accounts = data if isinstance(data, list) else data.get("accounts", [])
        print(f"[Calendar] Found {len(accounts)} connected accounts")

        # Find Google account
        google_account_id = None
        for acc in accounts:
            connection = acc.get("connection", "").lower()
            provider = acc.get("provider", "").lower()
            if "google" in connection or "google" in provider:
                google_account_id = acc.get("id")
                print(f"[Calendar] Found Google account: {google_account_id}")
                break

        if not google_account_id:
            print("[Calendar] No Google connected account found")
            return None

        # Get the token for this connected account
        token_url = f"{MYACCOUNT_BASE_URL}/connected-accounts/accounts/{google_account_id}/token"
        print(f"[Calendar] Fetching Google token from: {token_url}")

        token_response = requests.get(
            token_url,
            headers={
                "Authorization": f"Bearer {myaccount_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )

        print(f"[Calendar] Token endpoint response status: {token_response.status_code}")

        if token_response.status_code == 200:
            token_data = token_response.json()
            access_token = token_data.get("access_token")
            if access_token:
                print("[Calendar] Successfully retrieved Google access token")
                return access_token
            print(f"[Calendar] Token response missing access_token: {token_data}")
            return None

        print(f"[Calendar] Token endpoint error: {token_response.text}")

        # Handle specific errors
        if token_response.status_code == 404:
            raise CalendarError(
                "Connected Accounts token endpoint not found. This feature may not be enabled.",
                404
            )
        elif token_response.status_code == 401:
            raise CalendarError(
                "MyAccount token expired or invalid. Please re-authenticate.",
                401
            )

        return None

    except requests.RequestException as e:
        print(f"[Calendar] Request error: {e}")
        raise CalendarError(f"Failed to retrieve Google token: {str(e)}")


def check_google_connected(myaccount_token: str) -> bool:
    """
    Check if user has connected their Google account.

    Args:
        myaccount_token: User's Auth0 MyAccount access token

    Returns:
        True if Google is connected, False otherwise
    """
    if not myaccount_token:
        return False

    try:
        accounts_url = f"{MYACCOUNT_BASE_URL}/connected-accounts/accounts"
        response = requests.get(
            accounts_url,
            headers={
                "Authorization": f"Bearer {myaccount_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )

        if response.status_code != 200:
            return False

        data = response.json()
        accounts = data if isinstance(data, list) else data.get("accounts", [])

        for acc in accounts:
            connection = acc.get("connection", "").lower()
            provider = acc.get("provider", "").lower()
            if "google" in connection or "google" in provider:
                return True

        return False

    except Exception as e:
        print(f"[Calendar] Error checking connection: {e}")
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
        calendar_id: Calendar ID (default: "primary")
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


# LangChain Tool Functions

def list_events_tool(myaccount_token: str) -> str:
    """
    Tool function to list calendar events using Connected Accounts.

    Args:
        myaccount_token: User's Auth0 MyAccount access token

    Returns:
        Formatted string of upcoming events or error message
    """
    try:
        if not myaccount_token:
            return (
                "Google Calendar is not connected. Please click 'Connect Calendar' "
                "to link your Google account."
            )

        google_token = get_google_token_via_connected_accounts(myaccount_token)

        if google_token is None:
            return (
                "Unable to access your Google Calendar. Please reconnect your Google account "
                "by clicking 'Connect Calendar'."
            )

        events = list_calendar_events(google_token)
        return format_events_for_display(events)

    except CalendarError as e:
        return f"Calendar error: {e.message}"
    except Exception as e:
        return f"Failed to retrieve calendar events: {str(e)}"


def create_event_tool(
    myaccount_token: str,
    summary: str,
    start_time: str,
    end_time: str,
    description: Optional[str] = None,
) -> str:
    """
    Tool function to create a calendar event using Connected Accounts.

    Args:
        myaccount_token: User's Auth0 MyAccount access token
        summary: Event title
        start_time: ISO format datetime string
        end_time: ISO format datetime string
        description: Optional event description

    Returns:
        Confirmation message or error
    """
    try:
        if not myaccount_token:
            return (
                "Google Calendar is not connected. Please click 'Connect Calendar' "
                "to link your Google account."
            )

        google_token = get_google_token_via_connected_accounts(myaccount_token)

        if google_token is None:
            return (
                "Unable to access your Google Calendar. Please reconnect your Google account."
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
