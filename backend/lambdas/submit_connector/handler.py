"""
Submit Connector Lambda — POST /api/v1/aws-connect/submit
5-step orchestration:
  1. Create destination S3 bucket in your account
  2. Write connector.json
  3. Attach bucket policy (allows Role B to replicate into it)
  4. Write replication rule to client's source bucket (via assumed Role A)
  5. Build CSV manifest + trigger S3 Batch Operations job for historical copy

Each step's result is logged; partial progress is preserved in S3 if Lambda times out.
"""

import json
import os
import uuid
import boto3
from botocore.exceptions import ClientError
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Access method strategies
# Parsed once from the request body; no if/else on access_method after this.
# ---------------------------------------------------------------------------


class AssumeRoleCredentials:
    """Cross-account access via STS AssumeRole. Supports live replication."""

    method = "assumeRole"

    def __init__(self, role_a_arn: str, role_b_arn: str):
        self.role_a_arn = role_a_arn
        self.role_b_arn = role_b_arn
        self.secrets_path = None  # not used for this method
        self._creds = None  # cached STS credentials for this invocation

    def _assume(self):
        """Assume Role A once and cache credentials for reuse."""
        if self._creds is None:
            sts = boto3.client("sts")
            self._creds = sts.assume_role(
                RoleArn=self.role_a_arn,
                RoleSessionName="finomateConnector",
                DurationSeconds=900,
            )["Credentials"]
        return self._creds

    def store_credentials(self, _client_id: str):
        """No-op — AssumeRole stores no long-lived credentials."""
        return None

    def get_source_s3_client(self, region: str, _session_name: str = None):
        """Assume Role A (cached) and return an S3 client for the client's account."""
        creds = self._assume()
        return boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=creds["AccessKeyId"],
            aws_secret_access_key=creds["SecretAccessKey"],
            aws_session_token=creds["SessionToken"],
        )

    def replication_principal(self) -> str:
        return self.role_b_arn

    def supports_live_replication(self) -> bool:
        return bool(self.role_a_arn and self.role_b_arn)

    def as_connector_fields(self) -> dict:
        return {
            "accessMethod": self.method,
            "roleArn": self.role_a_arn,
            "roleBArn": self.role_b_arn,
            "secretsPath": None,
        }


class DirectIAMCredentials:
    """Direct IAM access keys. No live replication — manual setup required."""

    method = "directIAM"

    def __init__(self, access_key_id: str, secret_access_key: str):
        self.access_key_id = access_key_id
        self.secret_access_key = secret_access_key
        self.secrets_path = None  # set by store_credentials()

    def store_credentials(self, client_id: str):
        """Store IAM keys in Secrets Manager. Returns error string or None."""
        self.secrets_path = f"/finomate/clients/{client_id}/aws-credentials"
        try:
            sm = boto3.client("secretsmanager")
            secret_value = json.dumps(
                {
                    "accessKeyId": self.access_key_id,
                    "secretAccessKey": self.secret_access_key,
                }
            )
            try:
                sm.create_secret(Name=self.secrets_path, SecretString=secret_value)
            except sm.exceptions.ResourceExistsException:
                sm.put_secret_value(
                    SecretId=self.secrets_path, SecretString=secret_value
                )
            return None
        except Exception as e:
            return (
                f"Step 2 failed — could not store credentials in Secrets Manager: {e}"
            )

    def get_source_s3_client(self, region: str, session_name: str = None):
        """Return an S3 client using the provided IAM keys."""
        return boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
        )

    def supports_live_replication(self) -> bool:
        return False

    def as_connector_fields(self) -> dict:
        return {
            "accessMethod": self.method,
            "roleArn": None,
            "roleBArn": None,
            "secretsPath": self.secrets_path,
        }


def _parse_credentials(body: dict):
    """
    Parse access method from the request body and return the appropriate
    credentials object. Returns None if the method is unrecognised.
    """
    method = body.get("accessMethod", "assumeRole")
    if method == "assumeRole":
        return AssumeRoleCredentials(
            role_a_arn=(body.get("roleArn") or "").strip(),
            role_b_arn=(body.get("roleBArn") or "").strip(),
        )
    if method == "directIAM":
        return DirectIAMCredentials(
            access_key_id=(body.get("accessKeyId") or "").strip(),
            secret_access_key=(body.get("secretAccessKey") or "").strip(),
        )
    return None


# ---------------------------------------------------------------------------
# Lambda handler
# ---------------------------------------------------------------------------


def lambda_handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except (json.JSONDecodeError, TypeError):
        return _response(400, {"error": "Invalid JSON body"})

    authorizer_ctx = event.get("requestContext", {}).get("authorizer", {})
    username = authorizer_ctx.get("username", "unknown")
    client_id = authorizer_ctx.get("client_id", "unknown")

    company_name = (body.get("companyName") or "").lower().strip().replace(" ", "-")
    email = (body.get("email") or "").strip()
    bucket_name = (body.get("bucketName") or "").strip()
    bucket_arn = (body.get("bucketArn") or "").strip()

    if not company_name or not bucket_name:
        return _response(
            400,
            {"error": "companyName and bucketName are required"},
        )

    creds = _parse_credentials(body)
    if creds is None:
        return _response(
            400, {"error": "Invalid accessMethod — must be 'assumeRole' or 'directIAM'"}
        )

    # Auto-detect source bucket region via get_bucket_location
    try:
        s3_probe = creds.get_source_s3_client("us-east-1")
        location = s3_probe.get_bucket_location(Bucket=bucket_name)
        region = location.get("LocationConstraint") or "us-east-1"
    except Exception as e:
        return _response(500, {"error": f"Cannot determine bucket region: {e}"})

    dest_bucket = f"{client_id}-billing-data"
    dest_bucket_arn = f"arn:aws:s3:::{dest_bucket}"
    my_account_id = os.environ.get("DESTINATION_ACCOUNT_ID", "")
    batch_role_arn = os.environ.get("BATCH_OPERATIONS_ROLE_ARN", "")
    aws_region = os.environ.get("AWS_REGION", "us-east-1")
    connector_id = str(uuid.uuid4())

    s3 = boto3.client("s3", region_name=aws_region)
    steps = []

    # ------------------------------------------------------------------
    # Step 1 — Create destination bucket
    # ------------------------------------------------------------------
    ok, err = _step_create_bucket(s3, dest_bucket, aws_region)
    if not ok:
        return _response(500, {"error": err, "steps": steps})
    steps.append({"step": "create_bucket", "status": "success", "bucket": dest_bucket})

    # ------------------------------------------------------------------
    # Step 2 — Store credentials (method-specific) + write connector.json
    # ------------------------------------------------------------------
    err = creds.store_credentials(connector_id)
    if err:
        return _response(500, {"error": err, "steps": steps})

    connector = _build_connector_record(
        connector_id,
        company_name,
        email,
        bucket_name,
        bucket_arn,
        region,
        dest_bucket,
        client_id,
        creds,
    )
    ok, err = _write_connector_json(s3, dest_bucket, connector)
    if not ok:
        return _response(500, {"error": err, "steps": steps})
    steps.append({"step": "write_connector_json", "status": "success"})

    # ------------------------------------------------------------------
    # Step 3 — Attach bucket policy to destination bucket (AssumeRole only)
    # DirectIAM skipped — no live replication, so no policy needed
    # ------------------------------------------------------------------
    if creds.supports_live_replication():
        step3 = _step_attach_bucket_policy(
            s3,
            dest_bucket,
            dest_bucket_arn,
            principal=creds.replication_principal(),
        )
        steps.append(step3)

    # ------------------------------------------------------------------
    # Step 4 — Write replication rule on client's source bucket
    # ------------------------------------------------------------------
    step4, replication_active = _step_write_replication_rule(
        s3,
        creds,
        dest_bucket,
        dest_bucket_arn,
        bucket_name,
        region,
        connector_id,
        my_account_id,
    )
    steps.append(step4)
    if replication_active:
        connector["replicationStatus"] = "active"

    # ------------------------------------------------------------------
    # Step 4b — Attach batch read policy to client's source bucket
    # ------------------------------------------------------------------
    step4b = _step_attach_source_bucket_policy(
        creds,
        bucket_name,
        bucket_arn,
        batch_role_arn,
        region,
    )
    steps.append(step4b)

    # ------------------------------------------------------------------
    # Step 5 — Build manifest + trigger S3 Batch Operations
    # ------------------------------------------------------------------
    step5 = _step_trigger_batch_copy(
        s3,
        creds,
        dest_bucket,
        dest_bucket_arn,
        bucket_name,
        region,
        company_name,
        my_account_id,
        batch_role_arn,
        aws_region,
        connector_id,
        connector,
    )
    steps.append(step5)

    # Final connector.json write (captures updated replicationStatus + batchJobId)
    try:
        s3.put_object(
            Bucket=dest_bucket,
            Key="_metadata/connector.json",
            Body=json.dumps(connector, indent=2),
            ContentType="application/json",
        )
    except Exception:
        pass

    return _response(
        200,
        {
            "status": "success",
            "connectorId": connector_id,
            "destinationBucket": dest_bucket,
            "steps": steps,
        },
    )


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


def _step_create_bucket(s3, dest_bucket, aws_region):
    """Create + configure the destination bucket. Returns (success, error_message)."""
    try:
        if aws_region == "us-east-1":
            s3.create_bucket(Bucket=dest_bucket)
        else:
            s3.create_bucket(
                Bucket=dest_bucket,
                CreateBucketConfiguration={"LocationConstraint": aws_region},
            )
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            return False, f"Step 1 failed — could not create bucket: {e}"
    else:
        s3.get_waiter("bucket_exists").wait(Bucket=dest_bucket)

    try:
        s3.put_bucket_versioning(
            Bucket=dest_bucket,
            VersioningConfiguration={"Status": "Enabled"},
        )
        s3.put_public_access_block(
            Bucket=dest_bucket,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        s3.put_bucket_encryption(
            Bucket=dest_bucket,
            ServerSideEncryptionConfiguration={
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "AES256"
                        },
                        "BucketKeyEnabled": True,
                    }
                ]
            },
        )
        s3.put_bucket_ownership_controls(
            Bucket=dest_bucket,
            OwnershipControls={"Rules": [{"ObjectOwnership": "BucketOwnerPreferred"}]},
        )
        for key in [
            "_metadata/.keep",
            "_batch/.keep",
            "_replication/.keep",
        ]:
            s3.put_object(Bucket=dest_bucket, Key=key, Body=b"")
    except Exception as e:
        return False, f"Step 1 failed — bucket configuration: {e}"

    return True, None


def _build_connector_record(
    connector_id,
    company_name,
    email,
    bucket_name,
    bucket_arn,
    region,
    dest_bucket,
    client_id,
    creds,
):
    return {
        "connectorId": connector_id,
        "companyName": company_name,
        "billingEmail": email,
        "sourceBucket": bucket_name,
        "sourceBucketArn": bucket_arn,
        "sourceRegion": region,
        "destinationBucket": dest_bucket,
        **creds.as_connector_fields(),
        "curFormat": "FOCUS_1.0_AWS_COLUMNS_PARQUET",
        "batchJobId": None,
        "batchJobStatus": "pending",
        "replicationStatus": "pending",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "connectedById": client_id,
    }


def _write_connector_json(s3, dest_bucket, connector):
    """Write connector.json to _metadata/. Returns (success, error_message)."""
    try:
        s3.put_object(
            Bucket=dest_bucket,
            Key="_metadata/connector.json",
            Body=json.dumps(connector, indent=2),
            ContentType="application/json",
        )
        return True, None
    except Exception as e:
        return False, f"Step 2 failed — could not write connector.json: {e}"


def _step_attach_bucket_policy(s3, dest_bucket, dest_bucket_arn, principal):
    """Attach a replication-allow policy to the destination bucket. Returns a step dict."""
    bucket_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowReplicationFromClientRole",
                "Effect": "Allow",
                "Principal": {"AWS": principal},
                "Action": [
                    "s3:ReplicateObject",
                    "s3:ReplicateDelete",
                    "s3:ReplicateTags",
                    "s3:GetBucketVersioning",
                    "s3:PutBucketVersioning",
                    "s3:List*",
                    "s3:ObjectOwnerOverrideToBucketOwner",
                ],
                "Resource": [dest_bucket_arn, f"{dest_bucket_arn}/*"],
            }
        ],
    }
    try:
        s3.put_bucket_policy(Bucket=dest_bucket, Policy=json.dumps(bucket_policy))
        return {"step": "attach_bucket_policy", "status": "success"}
    except Exception as e:
        return {"step": "attach_bucket_policy", "status": "failed", "error": str(e)}


def _step_write_replication_rule(
    s3,
    creds,
    dest_bucket,
    dest_bucket_arn,
    bucket_name,
    region,
    client_id,
    my_account_id,
):
    """
    Configure live replication on the client's source bucket.
    AssumeRole: assumes Role A, writes replication config via Role B.
    DirectIAM: skipped — live replication must be set up manually.
    Returns (step_dict, replication_active).
    """
    if not creds.supports_live_replication():
        s3.put_object(
            Bucket=dest_bucket,
            Key="_replication/status.json",
            Body=json.dumps(
                {"status": "pending", "reason": "Direct IAM — manual setup required"}
            ),
            ContentType="application/json",
        )
        return (
            {
                "step": "write_replication_rule",
                "status": "skipped",
                "reason": "Direct IAM method — live replication must be configured manually via the AWS console",
            },
            False,
        )

    try:
        client_s3 = creds.get_source_s3_client(region, "finomateConnectorSetup")
        client_s3.put_bucket_replication(
            Bucket=bucket_name,
            ReplicationConfiguration={
                "Role": creds.role_b_arn,
                "Rules": [
                    {
                        "ID": f"finomate-{client_id}",
                        "Priority": 1,
                        "Status": "Enabled",
                        "Filter": {"Prefix": ""},
                        "Destination": {
                            "Bucket": dest_bucket_arn,
                            "Account": my_account_id,
                            "AccessControlTranslation": {"Owner": "Destination"},
                        },
                        "DeleteMarkerReplication": {"Status": "Disabled"},
                    }
                ],
            },
        )
        s3.put_object(
            Bucket=dest_bucket,
            Key="_replication/status.json",
            Body=json.dumps(
                {
                    "status": "active",
                    "startedAt": datetime.now(timezone.utc).isoformat(),
                }
            ),
            ContentType="application/json",
        )
        return {"step": "write_replication_rule", "status": "success"}, True

    except Exception as e:
        s3.put_object(
            Bucket=dest_bucket,
            Key="_replication/status.json",
            Body=json.dumps({"status": "error", "error": str(e)}),
            ContentType="application/json",
        )
        return {
            "step": "write_replication_rule",
            "status": "failed",
            "error": str(e),
        }, False


def _step_attach_source_bucket_policy(creds, bucket_name, bucket_arn, batch_role_arn, region):
    """Grant finomate-batch-operations-role and Role B read access on the client's source bucket."""
    try:
        source_s3 = creds.get_source_s3_client(region, "finomateBatchPolicy")
        new_statements = [
            {
                "Sid": "AllowFinomateBatchOperations",
                "Effect": "Allow",
                "Principal": {"AWS": batch_role_arn},
                "Action": [
                    "s3:GetObject",
                    "s3:GetObjectVersion",
                    "s3:GetBucketLocation",
                    "s3:ListBucket",
                ],
                "Resource": [bucket_arn, f"{bucket_arn}/*"],
            },
        ]
        if creds.supports_live_replication():
            new_statements.append({
                "Sid": "AllowFinomateReplicationRoleB",
                "Effect": "Allow",
                "Principal": {"AWS": creds.role_b_arn},
                "Action": [
                    "s3:GetObjectVersionForReplication",
                    "s3:GetObjectVersionAcl",
                    "s3:GetObjectVersionTagging",
                    "s3:ListBucket",
                    "s3:GetBucketVersioning",
                    "s3:GetReplicationConfiguration",
                ],
                "Resource": [bucket_arn, f"{bucket_arn}/*"],
            })
        new_sids = {s["Sid"] for s in new_statements}
        try:
            existing = json.loads(source_s3.get_bucket_policy(Bucket=bucket_name)["Policy"])
            statements = [s for s in existing.get("Statement", []) if s.get("Sid") not in new_sids]
            statements.extend(new_statements)
            policy = {"Version": "2012-10-17", "Statement": statements}
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchBucketPolicy":
                policy = {"Version": "2012-10-17", "Statement": new_statements}
            else:
                raise
        source_s3.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(policy))
        return {"step": "attach_source_bucket_policy", "status": "success"}
    except Exception as e:
        return {"step": "attach_source_bucket_policy", "status": "failed", "error": str(e)}


def _step_trigger_batch_copy(
    s3,
    creds,
    dest_bucket,
    dest_bucket_arn,
    bucket_name,
    region,
    company_name,
    my_account_id,
    batch_role_arn,
    aws_region,
    connector_id,
    connector,
):
    """
    List source objects, upload a CSV manifest, and create an S3 Batch Operations job.
    Updates connector dict in-place with batchJobId on success.
    Returns a step dict.
    """
    try:
        source_s3 = creds.get_source_s3_client(region, "finomateBatchManifest")

        manifest_lines = []
        paginator = source_s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket_name):
            for obj in page.get("Contents", []):
                key = obj["Key"].replace('"', '""')
                manifest_lines.append(f'{bucket_name},"{key}"')

        if not manifest_lines:
            s3.put_object(
                Bucket=dest_bucket,
                Key="_batch/status.json",
                Body=json.dumps(
                    {"status": "pending", "reason": "No source objects found yet"}
                ),
                ContentType="application/json",
            )
            return {
                "step": "trigger_batch_copy",
                "status": "skipped",
                "reason": "No objects found in source bucket — historical copy will start once CUR files are generated",
            }

        manifest_resp = s3.put_object(
            Bucket=dest_bucket,
            Key="_batch/manifest.csv",
            Body="\n".join(manifest_lines).encode("utf-8"),
            ContentType="text/csv",
        )
        manifest_etag = manifest_resp["ETag"].strip('"')

        s3control = boto3.client("s3control", region_name=aws_region)
        job_resp = s3control.create_job(
            AccountId=my_account_id,
            ConfirmationRequired=False,
            Operation={
                "S3PutObjectCopy": {
                    "TargetResource": dest_bucket_arn,
                    "StorageClass": "STANDARD",
                    "MetadataDirective": "COPY",
                    "NewObjectTagging": [],
                }
            },
            Manifest={
                "Spec": {
                    "Format": "S3BatchOperations_CSV_20180820",
                    "Fields": ["Bucket", "Key"],
                },
                "Location": {
                    "ObjectArn": f"arn:aws:s3:::{dest_bucket}/_batch/manifest.csv",
                    "ETag": manifest_etag,
                },
            },
            Report={
                "Bucket": dest_bucket_arn,
                "Format": "Report_CSV_20180820",
                "Enabled": True,
                "Prefix": "_batch/reports",
                "ReportScope": "AllTasks",
            },
            Priority=10,
            RoleArn=batch_role_arn,
            Description=f"finomate historical CUR copy for {connector_id}:{company_name}",
            ClientRequestToken=connector_id,
        )

        batch_job_id = job_resp["JobId"]
        connector["batchJobId"] = batch_job_id
        connector["batchJobStatus"] = "running"

        s3.put_object(
            Bucket=dest_bucket,
            Key="_batch/status.json",
            Body=json.dumps(
                {
                    "status": "running",
                    "jobId": batch_job_id,
                    "objectCount": len(manifest_lines),
                    "startedAt": datetime.now(timezone.utc).isoformat(),
                }
            ),
            ContentType="application/json",
        )
        return {
            "step": "trigger_batch_copy",
            "status": "success",
            "jobId": batch_job_id,
            "objectCount": len(manifest_lines),
        }

    except Exception as e:
        s3.put_object(
            Bucket=dest_bucket,
            Key="_batch/status.json",
            Body=json.dumps({"status": "failed", "error": str(e)}),
            ContentType="application/json",
        )
        return {"step": "trigger_batch_copy", "status": "failed", "error": str(e)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
