"""
Google Calendar Integration Module

Integrates with Google Calendar via Auth0 MyAccount token vaulting (Connected Accounts).
"""

import os
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

import requests

# Configuration
AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "violet-hookworm-18506.cic-demo-platform.auth0app.com")
MYACCOUNT_BASE_URL = f"https://{AUTH0_DOMAIN}/me"

GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"


class CalendarError(Exception):
    """Calendar operation error."""

    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def get_google_token_from_myaccount(user_access_token: str) -> Optional[str]:
    """
    Fetch vaulted Google OAuth token from Auth0 MyAccount API.

    The user must have connected their Google account via Auth0 Connected Accounts
    (Social Connection with token vaulting enabled).

    Args:
        user_access_token: User's Auth0 access token (must have MyAccount audience)

    Returns:
        Google OAuth access token or None if not connected

    Raises:
        CalendarError: If API call fails
    """
    try:
        response = requests.get(
            f"{MYACCOUNT_BASE_URL}/linked-accounts",
            headers={
                "Authorization": f"Bearer {user_access_token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )

        if response.status_code == 401:
            raise CalendarError("Invalid or expired access token", 401)

        if response.status_code != 200:
            raise CalendarError(
                f"MyAccount API error: {response.status_code}",
                response.status_code
            )

        accounts = response.json()

        # Find Google account
        google_account = next(
            (acc for acc in accounts if acc.get("provider") == "google-oauth2"),
            None
        )

        if google_account is None:
            return None

        return google_account.get("access_token")

    except requests.RequestException as e:
        raise CalendarError(f"Failed to fetch linked accounts: {str(e)}")


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

def list_events_tool(user_access_token: str) -> str:
    """
    LangChain tool function to list calendar events.

    Args:
        user_access_token: User's Auth0 access token

    Returns:
        Formatted string of upcoming events or error message
    """
    try:
        google_token = get_google_token_from_myaccount(user_access_token)

        if google_token is None:
            return (
                "Google Calendar is not connected. Please connect your Google account "
                "in your profile settings to enable calendar features."
            )

        events = list_calendar_events(google_token)
        return format_events_for_display(events)

    except CalendarError as e:
        return f"Calendar error: {e.message}"
    except Exception as e:
        return f"Failed to retrieve calendar events: {str(e)}"


def create_event_tool(
    user_access_token: str,
    summary: str,
    start_time: str,
    end_time: str,
    description: Optional[str] = None,
) -> str:
    """
    LangChain tool function to create a calendar event.

    Args:
        user_access_token: User's Auth0 access token
        summary: Event title
        start_time: ISO format datetime string
        end_time: ISO format datetime string
        description: Optional event description

    Returns:
        Confirmation message or error
    """
    try:
        google_token = get_google_token_from_myaccount(user_access_token)

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
