"""
Lambda Function URL Handler

Main entry point for the RAG Health agent API.
Supports both BFF session-based auth (primary) and legacy JWT auth (fallback).

BFF Pattern:
- OAuth endpoints: /auth/login, /auth/callback, /auth/logout, /auth/me
- Session-based auth via HTTP-only cookies
- Calendar tokens retrieved from Auth0 Token Vault

Calendar integration uses Auth0 Token Vault to retrieve Google access tokens.
"""

import json
import os
from typing import Any, Dict, Optional

from chains import create_rag_chain
from google_calendar import (
    CalendarError,
    list_calendar_events,
    create_calendar_event,
    format_events_for_display,
)
from bff_session import (
    SessionError,
    validate_session,
    extract_session_id_from_cookie,
    get_user_context as get_session_user_context,
    build_session_cookie,
    build_clear_session_cookie,
)
from oauth_handler import (
    handle_login,
    handle_callback,
    handle_logout,
    handle_me,
    handle_connect_google,
    handle_connect_callback,
    get_google_token_from_connected_accounts,
    get_google_token_via_token_exchange,
)

# Legacy auth imports (kept for backward compatibility during migration)
from auth0_jwt import (
    AuthError,
    extract_bearer_token,
    validate_auth0_token,
    get_user_context as get_jwt_user_context,
)

# Configuration
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")

# CORS headers for Function URL responses (cross-origin with credentials)
def get_cors_headers(include_credentials: bool = True) -> Dict[str, str]:
    """Get CORS headers for responses."""
    headers = {
        "Access-Control-Allow-Origin": FRONTEND_ORIGIN,
        "Access-Control-Allow-Headers": "authorization,content-type,x-requested-with",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Content-Type": "application/json",
    }
    if include_credentials:
        headers["Access-Control-Allow-Credentials"] = "true"
    return headers


CORS_HEADERS = get_cors_headers()


def create_response(
    status_code: int,
    body: Dict[str, Any],
    extra_headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Create a Lambda Function URL response."""
    headers = CORS_HEADERS.copy()
    if extra_headers:
        headers.update(extra_headers)

    return {
        "statusCode": status_code,
        "headers": headers,
        "body": json.dumps(body),
    }


def handle_options() -> Dict[str, Any]:
    """Handle CORS preflight requests."""
    return {
        "statusCode": 200,
        "headers": CORS_HEADERS,
        "body": "",
    }


def get_auth_context(event: Dict[str, Any]) -> tuple[Dict[str, Any], Optional[str]]:
    """
    Get authentication context from session cookie or Bearer token.

    Tries session-based auth first (BFF pattern), falls back to JWT.

    Args:
        event: Lambda event

    Returns:
        (user_context, error_message) - user_context if authenticated, error if not

    Raises:
        AuthError: If authentication fails
    """
    headers = event.get("headers", {})

    # Try session-based auth first (BFF pattern)
    cookie_header = headers.get("cookie") or headers.get("Cookie")
    session_id = extract_session_id_from_cookie(cookie_header)

    if session_id:
        session = validate_session(session_id)
        if session:
            user_context = get_session_user_context(session)
            # Add session reference for calendar operations
            user_context["_session"] = session
            return user_context, None

    # Fall back to Bearer token auth (legacy)
    auth_header = headers.get("authorization") or headers.get("Authorization")
    if auth_header:
        try:
            token = extract_bearer_token(auth_header)
            claims = validate_auth0_token(token)
            user_context = get_jwt_user_context(claims)
            return user_context, None
        except AuthError as e:
            raise e

    # No authentication provided
    raise AuthError("Authentication required", 401)


def handle_query(user_context: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle RAG query requests.

    Args:
        user_context: Authenticated user context
        body: Request body with 'query' field

    Returns:
        Response with generated answer
    """
    query = body.get("query", "").strip()

    if not query:
        return create_response(400, {"error": "Query is required"})

    try:
        # Create RAG chain for this user (with FGA ABAC filtering)
        chain = create_rag_chain(
            user_id=user_context["user_id"],
            subscription_tier=user_context["subscription_tier"],
            roles=user_context.get("roles", []),
        )

        # Run the chain
        response = chain.invoke(query)

        return create_response(200, {
            "answer": response,
            "user_tier": user_context["subscription_tier"],
        })

    except Exception as e:
        print(f"Query error: {str(e)}")
        return create_response(500, {"error": f"Failed to process query: {str(e)}"})


def handle_calendar_list_bff(user_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle calendar event listing using Federated Token Exchange (BFF pattern).

    Uses Auth0 Federated Token Exchange to get Google access token from the
    user's Auth0 access token. Falls back to Connected Accounts if available.

    Args:
        user_context: Authenticated user context

    Returns:
        Response with calendar events
    """
    session = user_context.get("_session", {})
    refresh_token = session.get("refresh_token")
    user_id = user_context.get("user_id", "")

    try:
        # Check if user logged in via Google
        if not user_id.startswith("google-oauth2|"):
            print(f"[Calendar] User {user_id} did not log in via Google")
            return create_response(200, {
                "events": "Google Calendar requires logging in with a Google account.",
                "error": "not_google_user",
            })

        if not refresh_token:
            print("[Calendar] No refresh token in session")
            return create_response(200, {
                "events": "Session expired. Please log in again.",
                "error": "no_refresh_token",
            })

        # Get Google token via Federated Token Exchange
        google_token = get_google_token_via_token_exchange(refresh_token)

        if not google_token:
            return create_response(200, {
                "events": "Unable to access your Google Calendar. Please reconnect your Google account.",
                "error": "token_exchange_failed",
            })

        # List calendar events
        events = list_calendar_events(google_token)
        events_display = format_events_for_display(events)

        return create_response(200, {
            "events": events_display,
        })

    except CalendarError as e:
        print(f"Calendar list error: {e.message}")
        return create_response(e.status_code, {"error": e.message})
    except Exception as e:
        print(f"Calendar list error: {str(e)}")
        return create_response(500, {"error": f"Failed to list calendar events: {str(e)}"})


def handle_calendar_create_bff(
    user_context: Dict[str, Any],
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Handle calendar event creation using Connected Accounts API (BFF pattern).

    Args:
        user_context: Authenticated user context
        body: Request body with event details

    Returns:
        Response with created event confirmation
    """
    session = user_context.get("_session", {})
    refresh_token = session.get("refresh_token")
    user_id = user_context.get("user_id", "")

    summary = body.get("summary", "").strip()
    start_time = body.get("start_time", "").strip()
    end_time = body.get("end_time", "").strip()
    description = body.get("description")

    if not summary or not start_time or not end_time:
        return create_response(400, {
            "error": "summary, start_time, and end_time are required"
        })

    try:
        # Check if user logged in via Google
        if not user_id.startswith("google-oauth2|"):
            return create_response(400, {
                "error": "Google Calendar requires logging in with a Google account.",
            })

        if not refresh_token:
            return create_response(400, {
                "error": "Session expired. Please log in again.",
            })

        # Get Google token via Federated Token Exchange
        google_token = get_google_token_via_token_exchange(refresh_token)

        if not google_token:
            return create_response(400, {
                "error": "Unable to access Google Calendar. Please reconnect your Google account.",
            })

        # Parse datetime strings
        from datetime import datetime
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))

        # Create event
        event = create_calendar_event(
            google_token=google_token,
            summary=summary,
            start_time=start_dt,
            end_time=end_dt,
            description=description,
        )

        event_link = event.get("htmlLink", "")
        return create_response(200, {
            "result": f"Event '{summary}' created successfully!",
            "event_link": event_link,
        })

    except CalendarError as e:
        print(f"Calendar create error: {e.message}")
        return create_response(e.status_code, {"error": e.message})
    except ValueError as e:
        return create_response(400, {"error": f"Invalid date format: {str(e)}"})
    except Exception as e:
        print(f"Calendar create error: {str(e)}")
        return create_response(500, {"error": f"Failed to create calendar event: {str(e)}"})


def handle_chat(user_context: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle conversational chat requests with RAG and calendar tools.

    This endpoint provides a unified chat interface that can:
    - Answer gut health questions using RAG
    - List calendar events
    - Create calendar events

    Calendar access uses Connected Accounts API via MyAccount token.

    Args:
        user_context: Authenticated user context
        body: Request body with 'message' field

    Returns:
        Response with chat answer
    """
    message = body.get("message", "").strip()

    if not message:
        return create_response(400, {"error": "Message is required"})

    try:
        # Simple intent detection
        message_lower = message.lower()

        # Check for calendar-related intents
        calendar_keywords = ["calendar", "schedule", "appointment", "event", "meeting", "meetings"]
        if any(keyword in message_lower for keyword in calendar_keywords):
            # Get refresh token and user_id from session
            session = user_context.get("_session", {})
            refresh_token = session.get("refresh_token")
            user_id = user_context.get("user_id", "")

            # Check if user logged in via Google
            if not user_id.startswith("google-oauth2|"):
                return create_response(200, {
                    "answer": (
                        "Google Calendar requires logging in with a Google account. "
                        "Please log out and log back in with Google."
                    ),
                    "intent": "calendar_not_google_user",
                })

            if not refresh_token:
                return create_response(200, {
                    "answer": "Session expired. Please log in again.",
                    "intent": "session_expired",
                })

            # Try to get Google token via Federated Token Exchange
            try:
                google_token = get_google_token_via_token_exchange(refresh_token)
            except Exception as e:
                print(f"[Chat] Error getting Google token: {e}")
                google_token = None

            if not google_token:
                return create_response(200, {
                    "answer": (
                        "Unable to access your Google Calendar. "
                        "Please reconnect your Google account."
                    ),
                    "intent": "calendar_auth_required",
                })

            if any(keyword in message_lower for keyword in ["what", "list", "show", "upcoming", "do i have", "any"]):
                # List events using Connected Accounts
                try:
                    events = list_calendar_events(google_token)
                    events_display = format_events_for_display(events)
                    return create_response(200, {
                        "answer": events_display,
                        "intent": "calendar_list",
                    })
                except CalendarError as e:
                    return create_response(200, {
                        "answer": f"Failed to retrieve calendar events: {e.message}",
                        "intent": "calendar_error",
                    })

            # For create, guide user to provide structured input
            elif any(keyword in message_lower for keyword in ["create", "add", "schedule", "book"]):
                return create_response(200, {
                    "answer": (
                        "I can help you schedule an appointment! Please provide the following details:\n\n"
                        "- **Event name**: What would you like to call this event?\n"
                        "- **Start time**: When should it start? (e.g., 2024-03-15T14:00:00)\n"
                        "- **End time**: When should it end?\n"
                        "- **Description**: (optional) Any additional details?\n\n"
                        "Or use the /calendar/create endpoint with structured data."
                    ),
                    "intent": "calendar_create_prompt",
                })

        # Default to RAG query for gut health content (with FGA ABAC filtering)
        chain = create_rag_chain(
            user_id=user_context["user_id"],
            subscription_tier=user_context["subscription_tier"],
            roles=user_context.get("roles", []),
        )
        response = chain.invoke(message)

        return create_response(200, {
            "answer": response,
            "intent": "rag_query",
            "user_tier": user_context["subscription_tier"],
        })

    except Exception as e:
        print(f"Chat error: {str(e)}")
        return create_response(500, {"error": f"Failed to process message: {str(e)}"})


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda Function URL handler.

    Auth Endpoints (no auth required):
    - POST /auth/login - Initiate OAuth flow
    - GET /auth/callback - OAuth callback
    - POST /auth/logout - End session
    - GET /auth/me - Get current user

    API Endpoints (session or Bearer token required):
    - POST /query - RAG query
    - POST /chat - Conversational chat with RAG and calendar
    - GET /calendar - List calendar events
    - POST /calendar/create - Create calendar event

    Utility Endpoints:
    - GET /health - Health check (no auth)
    """
    # Handle CORS preflight
    http_method = event.get("requestContext", {}).get("http", {}).get("method", "")
    if http_method == "OPTIONS":
        return handle_options()

    # Extract path
    path = event.get("rawPath", "/")

    # Health check - no auth required
    if path == "/health":
        return create_response(200, {
            "status": "healthy",
            "environment": os.environ.get("ENVIRONMENT", "unknown"),
            "auth_mode": "bff",
        })

    # OAuth endpoints - no auth required (they establish auth)
    if path == "/auth/login" and http_method == "POST":
        response = handle_login(event)
        response["headers"] = {**CORS_HEADERS, **response.get("headers", {})}
        return response

    if path == "/auth/callback" and http_method == "GET":
        response = handle_callback(event)
        response["headers"] = {**CORS_HEADERS, **response.get("headers", {})}
        return response

    if path == "/auth/logout" and http_method == "POST":
        response = handle_logout(event)
        response["headers"] = {**CORS_HEADERS, **response.get("headers", {})}
        return response

    if path == "/auth/me" and http_method == "GET":
        response = handle_me(event)
        response["headers"] = {**CORS_HEADERS, **response.get("headers", {})}
        return response

    # Connected Accounts flow - requires existing session
    if path == "/auth/connect/google" and http_method == "POST":
        response = handle_connect_google(event)
        response["headers"] = {**CORS_HEADERS, **response.get("headers", {})}
        return response

    if path == "/auth/connect/callback" and http_method == "GET":
        response = handle_connect_callback(event)
        response["headers"] = {**CORS_HEADERS, **response.get("headers", {})}
        return response

    # Protected endpoints - require authentication
    try:
        user_context, error = get_auth_context(event)
        if error:
            return create_response(401, {"error": error})
    except AuthError as e:
        return create_response(e.status_code, {"error": e.error})

    # Parse request body
    body = {}
    if event.get("body"):
        try:
            body = json.loads(event["body"])
        except json.JSONDecodeError:
            return create_response(400, {"error": "Invalid JSON body"})

    # Route to appropriate handler
    try:
        if path == "/query" and http_method == "POST":
            return handle_query(user_context, body)

        elif path == "/chat" and http_method == "POST":
            return handle_chat(user_context, body)

        elif path == "/calendar" and http_method == "GET":
            return handle_calendar_list_bff(user_context)

        elif path == "/calendar/create" and http_method == "POST":
            return handle_calendar_create_bff(user_context, body)

        else:
            return create_response(404, {"error": f"Not found: {http_method} {path}"})

    except AuthError as e:
        return create_response(e.status_code, {"error": e.error})
    except Exception as e:
        print(f"Handler error: {str(e)}")
        return create_response(500, {"error": "Internal server error"})
