"""
Lambda Function URL Handler

Main entry point for the RAG Health agent API.
Handles Auth0 JWT validation, RAG queries, and Google Calendar operations.

Calendar integration uses Auth0 Connected Accounts API to retrieve
Google access tokens for calendar operations.
"""

import json
import os
from typing import Any, Dict

from auth0_jwt import (
    AuthError,
    extract_bearer_token,
    validate_auth0_token,
    get_user_context,
    require_scope,
)
from chains import create_rag_chain
from google_calendar import (
    CalendarError,
    list_events_tool,
    create_event_tool,
)

# CORS headers for Function URL responses
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "authorization,content-type,x-requested-with",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
    "Content-Type": "application/json",
}


def create_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    """Create a Lambda Function URL response."""
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body),
    }


def handle_options() -> Dict[str, Any]:
    """Handle CORS preflight requests."""
    return {
        "statusCode": 200,
        "headers": CORS_HEADERS,
        "body": "",
    }


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


def handle_calendar_list(
    user_context: Dict[str, Any],
    myaccount_token: str
) -> Dict[str, Any]:
    """
    Handle calendar event listing requests.

    Uses Auth0 Connected Accounts API to retrieve Google access token.

    Args:
        user_context: Authenticated user context
        myaccount_token: User's Auth0 MyAccount token

    Returns:
        Response with calendar events
    """
    if not myaccount_token:
        return create_response(400, {
            "error": "MyAccount token required. Please connect your Google Calendar first."
        })

    try:
        events_display = list_events_tool(myaccount_token)

        return create_response(200, {
            "events": events_display,
        })

    except Exception as e:
        print(f"Calendar list error: {str(e)}")
        return create_response(500, {"error": f"Failed to list calendar events: {str(e)}"})


def handle_calendar_create(
    user_context: Dict[str, Any],
    body: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Handle calendar event creation requests.

    Uses Auth0 Connected Accounts API to retrieve Google access token.

    Args:
        user_context: Authenticated user context
        body: Request body with event details and myaccount_token

    Returns:
        Response with created event confirmation
    """
    myaccount_token = body.get("myaccount_token", "")
    summary = body.get("summary", "").strip()
    start_time = body.get("start_time", "").strip()
    end_time = body.get("end_time", "").strip()
    description = body.get("description")

    if not myaccount_token:
        return create_response(400, {
            "error": "myaccount_token is required for calendar operations"
        })

    if not summary or not start_time or not end_time:
        return create_response(400, {
            "error": "summary, start_time, and end_time are required"
        })

    try:
        result = create_event_tool(
            myaccount_token=myaccount_token,
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            description=description,
        )

        return create_response(200, {
            "result": result,
        })

    except Exception as e:
        print(f"Calendar create error: {str(e)}")
        return create_response(500, {"error": f"Failed to create calendar event: {str(e)}"})


def handle_chat(user_context: Dict[str, Any], access_token: str, body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Handle conversational chat requests with RAG and calendar tools.

    This endpoint provides a unified chat interface that can:
    - Answer gut health questions using RAG
    - List calendar events
    - Create calendar events

    Calendar access uses Auth0 Connected Accounts API to retrieve Google access token.

    Args:
        user_context: Authenticated user context
        access_token: User's Auth0 access token (for API authorization)
        body: Request body with 'message' field, optional 'myaccount_token'

    Returns:
        Response with chat answer
    """
    message = body.get("message", "").strip()
    # MyAccount token for Connected Accounts API
    myaccount_token = body.get("myaccount_token", "")

    if not message:
        return create_response(400, {"error": "Message is required"})

    try:
        # Simple intent detection
        message_lower = message.lower()

        # Check for calendar-related intents
        calendar_keywords = ["calendar", "schedule", "appointment", "event", "meeting", "meetings"]
        if any(keyword in message_lower for keyword in calendar_keywords):
            # Check if we have a MyAccount token for calendar access
            if not myaccount_token:
                return create_response(200, {
                    "answer": (
                        "To access your calendar, please connect your Google account first.\n\n"
                        "Click the 'Connect Calendar' button in the header to link your Google Calendar."
                    ),
                    "intent": "calendar_auth_required",
                })

            if any(keyword in message_lower for keyword in ["what", "list", "show", "upcoming", "do i have", "any"]):
                # List events using Connected Accounts
                events_display = list_events_tool(myaccount_token)
                return create_response(200, {
                    "answer": events_display,
                    "intent": "calendar_list",
                })
            # For create, we'd need more structured input - guide user
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

    Endpoints:
    - POST /query - RAG query (requires read:content scope)
    - POST /chat - Conversational chat with RAG and calendar
    - GET /calendar - List calendar events (requires read:calendar scope)
    - POST /calendar/create - Create calendar event (requires write:calendar scope)

    All endpoints require Auth0 Bearer token authentication.
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
        })

    # Get Authorization header
    headers = event.get("headers", {})
    auth_header = headers.get("authorization") or headers.get("Authorization")

    try:
        # Validate Auth0 token
        token = extract_bearer_token(auth_header)
        claims = validate_auth0_token(token)
        user_context = get_user_context(claims)

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
            require_scope(claims, "read:content")
            return handle_query(user_context, body)

        elif path == "/chat" and http_method == "POST":
            # Chat requires read:content, calendar scopes are optional
            require_scope(claims, "read:content")
            return handle_chat(user_context, token, body)

        elif path == "/calendar" and http_method == "GET":
            require_scope(claims, "read:calendar")
            # Get myaccount_token from query params or body (for Connected Accounts API)
            query_params = event.get("queryStringParameters", {}) or {}
            myaccount_token = query_params.get("myaccount_token", "") or body.get("myaccount_token", "")
            return handle_calendar_list(user_context, myaccount_token)

        elif path == "/calendar/create" and http_method == "POST":
            require_scope(claims, "write:calendar")
            return handle_calendar_create(user_context, body)

        else:
            return create_response(404, {"error": f"Not found: {http_method} {path}"})

    except AuthError as e:
        return create_response(e.status_code, {"error": e.error})
    except Exception as e:
        print(f"Handler error: {str(e)}")
        return create_response(500, {"error": "Internal server error"})
