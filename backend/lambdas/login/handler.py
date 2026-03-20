"""
Login Lambda — POST /api/v1/login
Validates credentials from USERS JSON env var, issues a signed JWT (HS256).
"""

import json
import os
import secrets
import time

import jwt


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except (json.JSONDecodeError, TypeError):
        return _response(400, {"error": "Invalid JSON body"})

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    if not username or not password:
        return _response(401, {"error": "Username and password required"})

    # Load and parse user list
    try:
        users = json.loads(os.environ.get("USERS", "[]"))
    except (json.JSONDecodeError, ValueError):
        return _response(500, {"error": "Server misconfiguration"})

    # Timing-safe credential check — compare_digest takes constant time regardless
    # of match position, preventing timing attacks
    authenticated_user = None
    for user in users:
        stored_name = user.get("name", "")
        stored_pass = user.get("password", "")
        if (
            stored_name
            and secrets.compare_digest(username.encode(), stored_name.encode())
            and secrets.compare_digest(password.encode(), stored_pass.encode())
        ):
            authenticated_user = user
            break

    if not authenticated_user:
        return _response(401, {"error": "Invalid credentials"})

    jwt_secret = os.environ.get("JWT_SECRET", "")
    if not jwt_secret:
        return _response(500, {"error": "Server misconfiguration"})

    token = jwt.encode(
        {"sub": username, "client_id": authenticated_user["client_id"], "exp": int(time.time()) + 86400},
        jwt_secret,
        algorithm="HS256",
    )
    return _response(200, {"token": token})


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.environ.get("ALLOWED_ORIGIN", "*"),
            "Access-Control-Allow-Headers": "Content-Type,Authorization",
            "Access-Control-Allow-Methods": "POST,OPTIONS",
        },
        "body": json.dumps(body),
    }
