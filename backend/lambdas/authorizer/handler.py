"""
Lambda Authorizer (REQUEST type) — attached to all protected routes.
Verifies HS256 JWT from Authorization: Bearer <token> header.
Returns IAM Allow/Deny policy. Passes username and client_id via context to downstream Lambdas.
"""
import os

import jwt


def lambda_handler(event, context):
    # Extract token from Authorization header (REQUEST type authorizer)
    headers = event.get("headers") or {}
    auth_header = (
        headers.get("Authorization")
        or headers.get("authorization")
        or ""
    )

    method_arn = event.get("methodArn", "*")
    # Allow all methods/paths on this API stage to avoid policy-scope 403s
    # when the same principal calls multiple endpoints
    arn_parts = method_arn.split(":")
    if len(arn_parts) >= 6:
        api_stage = "/".join(method_arn.split("/")[:2])
        wildcard_arn = api_stage + "/*/*"
    else:
        wildcard_arn = "*"

    if not auth_header.startswith("Bearer "):
        raise Exception("Unauthorized")

    token = auth_header[7:].strip()
    if not token:
        raise Exception("Unauthorized")

    jwt_secret = os.environ.get("JWT_SECRET", "")

    try:
        payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise Exception("Unauthorized")
    except jwt.InvalidTokenError:
        raise Exception("Unauthorized")

    username = payload.get("sub", "unknown")
    client_id = payload.get("client_id", "unknown")

    return {
        "principalId": username,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": "Allow",
                    "Resource": wildcard_arn,
                }
            ],
        },
        "context": {
            "username": username,
            "client_id": client_id,
        },
    }
