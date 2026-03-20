# Submit Connector — Step-by-Step Reference

> Source: `backend/lambdas/submit_connector/handler.py`

What happens end-to-end when a client submits the connector form.

---

## Step 1 — Create destination bucket (`_step_create_bucket`)

**What:** Creates an S3 bucket in Finomate's own AWS account to receive the client's billing data.

**Bucket name:** `{client_id}-billing-data` — derived from the `client_id` in the USERS JSON of the authenticated portal user. AWS account ID is not required — Finomate's own account ID is read from the Lambda environment (`DESTINATION_ACCOUNT_ID`).

**Configuration applied on creation:**

| Setting             | Value                | Why                                                                    |
| ------------------- | -------------------- | ---------------------------------------------------------------------- |
| Versioning          | Enabled              | Required for S3 replication to work — replication won't run without it |
| Public access block | All blocked          | Billing data must never be public                                      |
| Encryption          | SSE-S3 (AES256)      | Data at rest encrypted by default                                      |
| Object ownership    | BucketOwnerPreferred | Ensures Finomate owns replicated objects, not the client account       |

**Placeholder folders created:** `_metadata/`, `_batch/`, `_replication/`, `data/` — these give the bucket structure upfront so status files always have a home.

**Idempotent:** If the bucket already exists and is owned by Finomate, it continues — no error. Re-submitting the same connector is safe.

---

## Step 2 — Store credentials + write `connector.json`

Two things happen here, both branching on access method.

**Credentials storage (DirectIAM only):**
The client's IAM access key + secret are written to AWS Secrets Manager at `/finomate/clients/{connector_id}/aws-credentials`. They're never stored in S3 or logs — Secrets Manager encrypts them with KMS. AssumeRole skips this entirely (no long-lived credentials to store).

**`connector.json`:**
A JSON record written to `{dest_bucket}/_metadata/connector.json` that captures the full state of this connector at creation time:

```json
{
  "connectorId": "uuid",
  "companyName": "acme-corp",
  "billingEmail": "billing@acme.com",
  "sourceBucket": "acme-cur-exports",
  "sourceBucketArn": "arn:aws:s3:::acme-cur-exports",
  "sourceRegion": "ap-south-1",
  "destinationBucket": "clienta-billing-data",
  "accessMethod": "assumeRole",
  "roleArn": "arn:aws:iam::...",
  "roleBArn": "arn:aws:iam::...",
  "secretsPath": null,
  "batchJobId": null,
  "batchJobStatus": "pending",
  "replicationStatus": "pending"
}
```

This file is the source of truth for the connector's state. `batchJobId` and `replicationStatus` are updated in later steps and in the final write at the end of the handler.

---

## Step 3 — Attach bucket policy (`_step_attach_bucket_policy`)

**What:** Writes an IAM bucket policy onto the destination bucket that grants the client's replication role permission to write into it.

**AssumeRole only** — skipped entirely for DirectIAM (no live replication means no policy needed).

**Principal:** `role_b_arn` — the dedicated replication role in the client's account.

**Permissions granted:**

```
s3:ReplicateObject, s3:ReplicateDelete, s3:ReplicateTags  ← live replication writes
s3:GetBucketVersioning, s3:PutBucketVersioning            ← replication requires versioning access
s3:List*                                                   ← replication needs to list objects
s3:ObjectOwnerOverrideToBucketOwner                        ← required for cross-account ownership transfer
```

> **Cross-account note:** For cross-account replication, AWS requires both the destination bucket policy AND Role B's own IAM policy to allow the write actions. The destination bucket policy alone is sufficient for same-account replication but not for cross-account. Role B must have `s3:ReplicateObject`, `s3:ReplicateDelete`, `s3:ReplicateTags`, and `s3:ObjectOwnerOverrideToBucketOwner` on the destination bucket ARN in its own IAM policy.

**Non-fatal:** If this step fails it logs `"failed"` in the steps array but does **not** abort. Bucket policy can be retried without redoing the whole setup.

---

## Step 4 — Write replication rule (`_step_write_replication_rule`)

**What:** Configures live S3 replication on the **client's** source bucket so new billing files are automatically copied to Finomate's destination bucket going forward.

**AssumeRole path:**

1. Finomate assumes Role A (cross-account) to get temporary credentials for the client's account
2. Uses those credentials to call `put_bucket_replication` on the client's source bucket
3. The replication rule points to `{dest_bucket_arn}` with Role B as the replication IAM role — rule ID is `finomate-{connector_id}`
4. `DeleteMarkerReplication: Disabled` — source deletions are not mirrored (billing files are never deleted)

**DirectIAM path:** Skipped entirely — S3 replication requires a role ARN, which direct keys can't provide. The client must set up replication manually via the AWS Console.

**Status file written:** `_replication/status.json` — either `"active"`, `"error"`, or `"pending"` (skipped case).

---

## Step 4b — Attach source bucket policy (`_step_attach_source_bucket_policy`)

**What:** Writes permissions onto the **client's source bucket** (cross-account) granting Finomate's batch operations role and Role B read access.

**Two statements written:**

| Sid | Principal | Actions | Purpose |
|---|---|---|---|
| `AllowFinomateBatchOperations` | `finomate-batch-operations-role` | `s3:GetObject`, `GetObjectVersion`, `GetBucketLocation`, `ListBucket` | Historical copy via S3 Batch Operations |
| `AllowFinomateReplicationRoleB` | `role_b_arn` (client's Role B) | `s3:GetObjectVersionForReplication`, `GetObjectVersionAcl`, `GetObjectVersionTagging`, `ListBucket`, `GetBucketVersioning`, `GetReplicationConfiguration` | S3 reads source objects using Role B during live replication |

> **Why Role B needs to be in the source bucket policy:** For cross-account access, AWS requires an explicit bucket policy on the source — the role's IAM policy alone is not sufficient. Without this, S3 (acting as Role B) gets AccessDenied when trying to read source objects, and every object shows `FAILED` replication status. This is not needed for same-account replication.

**Role B statement only added** when `creds.supports_live_replication()` is true (AssumeRole method). DirectIAM skips it.

**Idempotent:** Re-submitting replaces both statements by Sid — safe to run multiple times.

---

## Step 5 — Trigger historical data copy (`_step_trigger_batch_copy`)

**What:** Copies all **existing** objects from the client's source bucket into Finomate's destination. Live replication (step 4) only handles new files going forward — this step backfills everything already there.

**Mechanism: S3 Batch Operations** — an AWS-managed service that processes bulk S3 operations from a CSV manifest. Runs asynchronously outside the Lambda (which has a 15-minute max).

**How it works:**

1. Lists all objects in the client's source bucket via `list_objects_v2` paginator
2. Builds a CSV manifest: `bucket-name,"object-key"` per line
3. Uploads the manifest to `{dest_bucket}/_batch/manifest.csv`
4. Creates an S3 Batch Operations job pointing at that manifest — job copies every listed object into the destination bucket root (matching source key structure, same as live replication)
5. Writes the returned `jobId` back into `connector.json` and `_batch/status.json`

The `batch_status` Lambda (triggered by EventBridge when the job completes) reads `jobId` from `_batch/status.json` and updates the status to `"complete"` or `"failed"`.

**Empty bucket case:** If no objects exist yet, the step is skipped gracefully with `"status": "pending"`. Once CUR files appear, live replication picks them up automatically.

---

## Flow diagram

```
Submit form
    │
    ├─ Step 1: Create dest bucket (Finomate account)
    │
    ├─ Step 2: Store creds + record connector.json
    │
    ├─ Step 3: Allow Role B → dest bucket writes (destination bucket policy)
    │
    ├─ Step 4: Wire live replication on client's bucket ──► new files flow automatically
    │
    ├─ Step 4b: Allow batch role + Role B → source bucket reads (source bucket policy)
    │
    └─ Step 5: Backfill existing files via Batch Operations ──► async job
                                                                    │
                                                          EventBridge → batch_status Lambda
                                                          updates _batch/status.json when done
```

Steps 1–3 operate entirely within Finomate's account.
Steps 4, 4b, and 5 reach into the client's account via assumed role or direct keys.

---

## Access method reference

| Capability                        | AssumeRole                | DirectIAM                        |
| --------------------------------- | ------------------------- | -------------------------------- |
| Credential storage                | None (no long-lived keys) | Secrets Manager                  |
| Dest bucket policy (step 3)       | Yes — grants Role B       | Skipped                          |
| Live replication (step 4)         | Automatic                 | Skipped — scheduled pull instead |
| Source bucket policy (step 4b)    | Yes — grants batch role + Role B | Only batch role           |
| Source S3 client                  | STS AssumeRole A          | IAM keys directly                |
