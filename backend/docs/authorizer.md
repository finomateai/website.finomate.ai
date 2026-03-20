# Lambda Authorizer

> Source: `backend/lambdas/authorizer/handler.py`

A Lambda Authorizer is an AWS API Gateway feature where a Lambda decides whether a request
is allowed before it ever reaches your business logic Lambda.

---

## Where it sits in the request flow

```
Browser
  │
  │  POST /api/v1/aws-connect/submit
  │  Authorization: Bearer <jwt>
  ▼
API Gateway
  │
  ├──► AuthorizerFunction (this file)
  │         verifies JWT
  │         returns Allow / Deny policy
  │
  └──► SubmitConnectorFunction  ◄── only reached if Allow
```

API Gateway calls the authorizer on every protected request. If it returns `Deny` (or raises
an exception), the request is rejected with 401/403 before your Lambda runs.

---

## The `methodArn` wildcard

```python
api_stage = "/".join(method_arn.split("/")[:2])
wildcard_arn = api_stage + "/*/*"
```

API Gateway caches authorizer responses for 5 minutes (`AuthorizerResultTtlInSeconds: 300`
in `template.yaml`). The cached policy is reused for subsequent requests from the same token.

If the policy were scoped to the exact endpoint (`/v1/aws-connect/submit POST`), the cached
policy would be rejected when the same token calls `/v1/aws-connect/test` — causing a 403
even with a valid JWT.

The wildcard `stage/*/*` means: allow this token to call **any** method on **any** route in
this API stage. One authorization, valid across all endpoints for the cache duration.

---

## Why `raise Exception("Unauthorized")` instead of returning a Deny policy

API Gateway Lambda Authorizers have two failure modes:

|                                   | Behaviour                    |
| --------------------------------- | ---------------------------- |
| `raise Exception("Unauthorized")` | Returns **401** Unauthorized |
| Return a Deny policy              | Returns **403** Forbidden    |

Raising the exception is deliberate — a missing or malformed token means unauthenticated (401),
not forbidden (403). AWS specifically recognises the string `"Unauthorized"` for this.

---

## What gets passed downstream

```python
"context": {
    "username": username,     # JWT "sub" claim
    "client_id": client_id,   # JWT "client_id" claim — matches "client_id" field in USERS JSON
}
```

API Gateway injects this `context` dict into `event.requestContext.authorizer` of every
downstream Lambda. This is how `submit_connector` knows which portal user made the request
without re-reading the JWT:

```python
# submit_connector/handler.py
authorizer_ctx = event.get("requestContext", {}).get("authorizer", {})
username  = authorizer_ctx.get("username", "unknown")
client_id = authorizer_ctx.get("client_id", "unknown")
```

---

## PyJWT does the heavy lifting

```python
payload = jwt.decode(token, jwt_secret, algorithms=["HS256"])
```

Three things happen in this one call:

1. **Signature verified** — recomputes HMAC-SHA256 and compares
2. **Expiry checked** — raises `ExpiredSignatureError` if `exp` is in the past
3. **Payload decoded** — returns the claims dict

The explicit `algorithms=["HS256"]` list is a PyJWT security requirement — without it, an
attacker could craft a token with `"alg": "none"` and bypass signature verification entirely
(algorithm confusion attack).
