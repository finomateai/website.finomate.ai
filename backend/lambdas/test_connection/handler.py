"""
Test Connection Lambda — POST /api/v1/aws-connect/test
Runs 4 sequential connectivity checks against the client's S3 bucket.
Auto-detects bucket region via get_bucket_location so the client doesn't
need to supply a region.
Returns a per-check result list for the frontend checklist UI.
"""
import json
import os
import boto3
from botocore.exceptions import ClientError


# ---------------------------------------------------------------------------
# Access method strategies
# Parsed once from the request body; no if/else on access_method after this.
# ---------------------------------------------------------------------------

class AssumeRoleCredentials:
    """Cross-account access via STS AssumeRole."""

    requires_source_versioning = True  # live replication needs versioning on source bucket

    def __init__(self, role_arn: str):
        self.role_arn = role_arn
        self._creds = None  # cached STS credentials for this invocation

    def _assume(self):
        """Assume Role A once and cache credentials for reuse."""
        if self._creds is None:
            sts = boto3.client("sts")
            self._creds = sts.assume_role(
                RoleArn=self.role_arn,
                RoleSessionName="finomateConnectivityTest",
                DurationSeconds=900,
            )["Credentials"]
        return self._creds

    def get_s3_client(self, region="us-east-1"):
        """
        Assume Role A and return an S3 client.
        Returns (s3_client, step1_check_dict) — step1_check reflects pass/fail.
        """
        check = {"label": "Assume cross-account role (Role A)", "passed": False, "error": None}
        if not self.role_arn:
            check["error"] = "Role A ARN is required for AssumeRole method"
            return None, check
        try:
            creds = self._assume()
            s3 = boto3.client(
                "s3",
                region_name=region,
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
            )
            check["passed"] = True
            return s3, check
        except ClientError as e:
            code = e.response["Error"]["Code"]
            msg = e.response["Error"]["Message"]
            check["error"] = (
                f"{code}: {msg}. "
                "Verify your AWS account ID is listed as a trusted principal in the role's trust policy."
            )
            return None, check

    def make_s3_client(self, region: str):
        """Build a new S3 client for the given region using cached credentials."""
        creds = self._creds  # already fetched by get_s3_client
        return boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )


class DirectIAMCredentials:
    """Direct IAM access keys — no role assumption step."""

    requires_source_versioning = False  # batch copy (pull) works without versioning

    def __init__(self, access_key_id: str, secret_access_key: str):
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key

    def get_s3_client(self, region="us-east-1"):
        """
        Build an S3 client from IAM keys.
        Returns (s3_client, None) — no Step 1 check for direct IAM.
        """
        s3 = boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
        )
        return s3, None

    def make_s3_client(self, region: str):
        """Build a new S3 client for the given region."""
        return boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
        )


def _parse_credentials(body: dict):
    """
    Parse access method from the request body and return the appropriate
    credentials object. Returns None if the method is unrecognised.
    """
    method = body.get("accessMethod", "assumeRole")
    if method == "assumeRole":
        return AssumeRoleCredentials(role_arn=body.get("roleArn", "").strip())
    if method == "directIAM":
        return DirectIAMCredentials(
            access_key_id=body.get("accessKeyId", "").strip(),
            secret_access_key=body.get("secretAccessKey", "").strip(),
        )
    return None


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except (json.JSONDecodeError, TypeError):
        return _response(400, {"status": "error", "message": "Invalid JSON body"})

    bucket_name = body.get("bucketName", "").strip()

    if not bucket_name:
        return _response(400, {"status": "error", "message": "bucketName is required"})

    creds = _parse_credentials(body)
    if creds is None:
        return _response(400, {"status": "error", "message": "Invalid accessMethod"})

    # ------------------------------------------------------------------
    # Step 1 — Acquire S3 client (bootstrap with us-east-1)
    # ------------------------------------------------------------------
    s3, step1_check = creds.get_s3_client("us-east-1")

    checks = []
    if step1_check is not None:
        checks.append(step1_check)
        if not step1_check["passed"]:
            remaining = _skipped(["Bucket accessible", "Versioning enabled", "Read access confirmed"])
            return _response(403, {"status": "error", "checks": checks + remaining})

    # ------------------------------------------------------------------
    # Auto-detect bucket region and rebuild client for correct region
    # ------------------------------------------------------------------
    s3, region_err = _detect_region_and_rebuild(s3, creds, bucket_name)
    if region_err:
        check_bucket = {"label": "Bucket accessible", "passed": False, "error": region_err}
        checks.append(check_bucket)
        return _response(403, {
            "status": "error",
            "checks": checks + _skipped(["Versioning enabled", "Read access confirmed"]),
        })

    # ------------------------------------------------------------------
    # Step 2 — Bucket accessible
    # ------------------------------------------------------------------
    check_bucket = _check_bucket_accessible(s3, bucket_name)
    checks.append(check_bucket)
    if not check_bucket["passed"]:
        return _response(403, {
            "status": "error",
            "checks": checks + _skipped(["Versioning enabled", "Read access confirmed"]),
        })

    # ------------------------------------------------------------------
    # Step 3 — Versioning enabled (AssumeRole only)
    # DirectIAM uses batch pull which doesn't require source versioning
    # ------------------------------------------------------------------
    if creds.requires_source_versioning:
        check_versioning = _check_versioning_enabled(s3, bucket_name)
        checks.append(check_versioning)
        if not check_versioning["passed"]:
            return _response(400, {
                "status": "error",
                "checks": checks + _skipped(["Read access confirmed"]),
            })

    # ------------------------------------------------------------------
    # Step 4 — Read access confirmed
    # ------------------------------------------------------------------
    check_read = _check_read_access(s3, bucket_name)
    checks.append(check_read)

    all_passed = all(c["passed"] for c in checks)
    return _response(
        200 if all_passed else 403,
        {"status": "ok" if all_passed else "error", "checks": checks},
    )


# ---------------------------------------------------------------------------
# Region detection
# ---------------------------------------------------------------------------

def _detect_region_and_rebuild(s3, creds, bucket_name: str):
    """
    Call get_bucket_location to find the bucket's actual region, then rebuild
    the S3 client for that region if different from the bootstrap region.
    Returns (s3_client, error_string_or_None).
    """
    try:
        resp = s3.get_bucket_location(Bucket=bucket_name)
        # AWS returns None for us-east-1
        region = resp.get("LocationConstraint") or "us-east-1"
        if region != "us-east-1":
            s3 = creds.make_s3_client(region)
        return s3, None
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("403", "AccessDenied"):
            return None, (
                "Access denied — add s3:GetBucketLocation to Role A's permissions policy"
            )
        if code in ("404", "NoSuchBucket"):
            return None, f"Bucket '{bucket_name}' not found — verify the bucket name"
        return None, f"{code}: {e.response['Error']['Message']}"


# ---------------------------------------------------------------------------
# Individual connectivity checks
# ---------------------------------------------------------------------------

def _check_bucket_accessible(s3, bucket_name) -> dict:
    check = {"label": "Bucket accessible", "passed": False, "error": None}
    try:
        s3.head_bucket(Bucket=bucket_name)
        check["passed"] = True
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("403", "AccessDenied"):
            check["error"] = "Access denied — check s3:ListBucket permission on the bucket policy"
        elif code in ("404", "NoSuchBucket"):
            check["error"] = f"Bucket '{bucket_name}' not found — verify the bucket name and region"
        else:
            check["error"] = f"{code}: {e.response['Error']['Message']}"
    return check


def _check_versioning_enabled(s3, bucket_name) -> dict:
    check = {"label": "Versioning enabled", "passed": False, "error": None}
    try:
        resp = s3.get_bucket_versioning(Bucket=bucket_name)
        status = resp.get("Status", "")
        if status == "Enabled":
            check["passed"] = True
        elif status == "Suspended":
            check["error"] = "Versioning is Suspended — re-enable it in S3 Console > Properties > Bucket Versioning"
        else:
            check["error"] = "Versioning is not enabled — enable it in S3 Console > Properties > Bucket Versioning"
    except ClientError as e:
        check["error"] = f"Cannot read versioning status: {e.response['Error']['Message']}"
    return check


def _check_read_access(s3, bucket_name) -> dict:
    check = {"label": "Read access confirmed", "passed": False, "error": None}
    try:
        s3.list_objects_v2(Bucket=bucket_name, MaxKeys=1)
        check["passed"] = True
    except ClientError as e:
        code = e.response["Error"]["Code"]
        check["error"] = (
            f"Cannot list objects ({code}) — ensure s3:ListBucket and s3:GetObject "
            "are in the role/user's permissions policy"
        )
    return check


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _skipped(labels: list) -> list:
    return [{"label": lbl, "passed": False, "error": "Skipped"} for lbl in labels]


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
