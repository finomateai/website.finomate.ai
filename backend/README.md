# Finomate Backend — AWS SAM

Python 3.12 serverless backend deployed via AWS SAM to `ap-south-1`. Exposes a REST API through API Gateway backed by five Lambda functions.

---

## Stack

| Resource             | Name                          |
| -------------------- | ----------------------------- |
| CloudFormation stack | `finomate`                    |
| API Gateway          | `finomate-api` (stage: `api`) |
| Region               | `ap-south-1`                  |

---

## API Endpoints

| Method | Path                         | Auth | Lambda                      | Purpose                        |
| ------ | ---------------------------- | ---- | --------------------------- | ------------------------------ |
| POST   | `/api/v1/login`              | None | `finomate-login`            | Issue JWT                      |
| POST   | `/api/v1/aws-connect/test`   | JWT  | `finomate-test-connection`  | Live bucket connectivity check |
| POST   | `/api/v1/aws-connect/submit` | JWT  | `finomate-submit-connector` | Full connector setup           |

EventBridge triggers `finomate-batch-status` on S3 Batch Operations job completion — no HTTP endpoint.

---

## Lambda Functions

### `finomate-login`

Validates credentials against the `USERS` parameter and returns a signed JWT (HS256, 24h expiry). Public — no authorizer attached.

### `finomate-authorizer`

REQUEST-type Lambda authorizer. Verifies the `Authorization: Bearer <token>` header on all protected routes and injects `username` and `client_id` into the request context for downstream Lambdas.

### `finomate-test-connection`

Runs up to 4 checks against the client's source S3 bucket and returns a per-check pass/fail list:

1. Acquire S3 client (AssumeRole → STS assume; DirectIAM → key-based)
2. Bucket accessible (`HeadBucket`)
3. Versioning enabled — AssumeRole only (live replication requires it)
4. Read access confirmed (`ListObjectsV2`)

### `finomate-submit-connector`

5-step connector orchestration (timeout: 900s, memory: 512 MB):

1. Create destination bucket `{client_id}-billing-data` in Finomate's account
2. Store credentials + write `_metadata/connector.json`
3. Attach replication bucket policy — AssumeRole only
4. Write S3 replication rule on client's source bucket — AssumeRole only
5. Build CSV manifest → trigger S3 Batch Operations job for historical copy

Supports two access methods:

| Method       | Live Replication          | Credentials stored                                                               |
| ------------ | ------------------------- | -------------------------------------------------------------------------------- |
| `assumeRole` | Yes (via Role A + Role B) | None — role ARNs only                                                            |
| `directIAM`  | No                        | IAM keys in Secrets Manager at `/finomate/clients/{connectorId}/aws-credentials` |

### `finomate-batch-status`

Triggered by EventBridge when an S3 Batch Operations job transitions to `Complete` or `Failed`. Finds the matching `*-billing-data` bucket by `jobId` and updates `_batch/status.json` and `_metadata/connector.json`.

---

## Destination Bucket Layout

Each connector creates a bucket named `{client_id}-billing-data`:

```
_metadata/connector.json   — connector record (access method, status, job ID, …)
_batch/manifest.csv        — CSV manifest for historical copy job
_batch/status.json         — batch job status
_batch/reports/            — S3 Batch Operations completion reports
_replication/status.json   — live replication status
```

---

## IAM Roles

| Role                             | Principal                          | Purpose                                                                            |
| -------------------------------- | ---------------------------------- | ---------------------------------------------------------------------------------- |
| `finomate-lambda-execution-role` | `lambda.amazonaws.com`             | Shared execution role for Test, Submit, and BatchStatus Lambdas                    |
| `finomate-batch-operations-role` | `batchoperations.s3.amazonaws.com` | Allows S3 Batch Operations to read source objects and write to destination buckets |

---

## Environment Variables

| Function                    | Variable                    | Source                            |
| --------------------------- | --------------------------- | --------------------------------- |
| All                         | `ALLOWED_ORIGIN`            | `AllowedOrigin` parameter         |
| `finomate-login`            | `USERS`, `JWT_SECRET`       | `Users`, `JwtSecret` parameters   |
| `finomate-authorizer`       | `JWT_SECRET`                | `JwtSecret` parameter             |
| `finomate-submit-connector` | `DESTINATION_ACCOUNT_ID`    | `!Sub "${AWS::AccountId}"`        |
| `finomate-submit-connector` | `BATCH_OPERATIONS_ROLE_ARN` | `!GetAtt BatchOperationsRole.Arn` |

---

## Deploy

**First time:**

```bash
cd backend
sam build
sam deploy --guided
```

**Subsequent deploys:**

```bash
./deploy.sh
```

`deploy.sh` runs `sam build` + `sam deploy` with the configured `Users`, `JwtSecret`, and `AllowedOrigin` values. Edit those values at the top of the script before deploying.

**Update env vars only (no redeploy):**

```bash
./update-env.sh
```

Patches Lambda environment variables directly via the AWS CLI. Safe to run at any time — `DESTINATION_ACCOUNT_ID` and `BATCH_OPERATIONS_ROLE_ARN` are resolved live from STS and CloudFormation so they are never wiped.

> **Note:** Never run `update-env.sh` with hardcoded secrets in a shared environment. The JWT secret and user passwords in the script are for development only.

---

## Project Structure

```
cloudctrl/
├── lambdas/
│   ├── authorizer/          handler.py, requirements.txt
│   ├── batch_status/        handler.py
│   ├── login/               handler.py, requirements.txt
│   ├── submit_connector/    handler.py
│   └── test_connection/     handler.py
├── template.yaml            SAM / CloudFormation template
├── samconfig.toml           SAM CLI defaults (stack name, region, capabilities)
├── deploy.sh                Build + deploy helper
└── update-env.sh            Live env var patch helper
```

`login` and `authorizer` have a `requirements.txt` for `PyJWT`. All other functions use only boto3 and the Python stdlib — no `requirements.txt` needed.
