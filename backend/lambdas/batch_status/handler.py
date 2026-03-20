"""
Batch Status Lambda — triggered by EventBridge on S3 Batch Operations job state change.
Finds the matching client bucket by jobId and updates _batch/status.json + connector.json.
"""
import json
import boto3
from datetime import datetime, timezone


def lambda_handler(event, context):
    detail = event.get("detail", {})
    job_id = detail.get("jobId") or detail.get("JobId")
    raw_status = detail.get("status") or detail.get("Status", "")

    if not job_id:
        print("No jobId in event — skipping")
        return

    final_status = "complete" if raw_status in ("Complete", "Completed") else "failed"
    print(f"Batch job {job_id} → {final_status}")

    s3 = boto3.client("s3")

    try:
        buckets = s3.list_buckets().get("Buckets", [])
    except Exception as e:
        print(f"Could not list buckets: {e}")
        raise

    for bucket in buckets:
        bucket_name = bucket["Name"]
        if not bucket_name.endswith("-billing-data"):
            continue

        try:
            obj = s3.get_object(Bucket=bucket_name, Key="_metadata/connector.json")
            connector = json.loads(obj["Body"].read())
        except Exception:
            continue  # bucket doesn't have a connector.json

        if connector.get("batchJobId") != job_id:
            continue

        # Found the client — update both status files
        now = datetime.now(timezone.utc).isoformat()

        s3.put_object(
            Bucket=bucket_name,
            Key="_batch/status.json",
            Body=json.dumps({
                "status": final_status,
                "jobId": job_id,
                "completedAt": now,
            }),
            ContentType="application/json",
        )

        connector["batchJobStatus"] = final_status
        s3.put_object(
            Bucket=bucket_name,
            Key="_metadata/connector.json",
            Body=json.dumps(connector, indent=2),
            ContentType="application/json",
        )

        print(f"Updated {bucket_name} — batchJobStatus={final_status}")
        return

    print(f"No client bucket found for jobId={job_id}")
