# Finomate Architecture Diagrams

---

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              BROWSER (User)                                 │
│                                                                             │
│   login.html  ──────────────────────────────────  aws-connect.html         │
│       │  fetch POST /v1/login                           │  fetch POST /v1/  │
└───────┼─────────────────────────────────────────────────┼───────────────────┘
        │                                                 │
        ▼                                                 ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                       API Gateway  (finomate-api)                         │
│                                                                           │
│   POST /v1/login          POST /v1/aws-connect/test    POST /v1/aws-      │
│   ── NO AUTH ──           ──── JWT REQUIRED ────       connect/submit     │
│         │                         │                    ── JWT REQUIRED ── │
│         │               ┌─────────┴────────────────────────┐              │
│         │               │     Lambda Authorizer             │              │
│         │               │  (runs before every protected     │              │
│         │               │   route — Allow or Deny)          │              │
│         │               └─────────┬────────────────────┬───┘              │
└─────────┼─────────────────────────┼────────────────────┼──────────────────┘
          │                         │                    │
          ▼                         ▼                    ▼
    ┌───────────┐           ┌──────────────┐    ┌─────────────────┐
    │   login   │           │  authorizer  │    │ test_connection │
    │  lambda   │           │   lambda     │    │    lambda       │
    └─────┬─────┘           └──────────────┘    └────────┬────────┘
          │ JWT                                          │
          └──► Browser                        ┌─────────┴──────────┐
                                              ▼                    ▼
                                        ┌─────────┐        ┌──────────────┐
                                        │   STS   │        │  S3 (client  │
                                        └─────────┘        │   bucket)    │
                                                           └──────────────┘

    ┌──────────────────┐
    │ submit_connector │◄── POST /v1/aws-connect/submit (via authorizer)
    │     lambda       │
    └────────┬─────────┘
             │
    ┌────────┴──────────────────────────────────────┐
    ▼          ▼            ▼           ▼           ▼
 ┌─────┐  ┌───────┐  ┌──────────┐  ┌──────┐  ┌─────────────┐
 │ STS │  │S3     │  │S3 (dest  │  │Secrets│  │S3 Batch     │
 │     │  │(client│  │bucket)   │  │Manager│  │Operations   │
 └─────┘  │bucket)│  └──────────┘  └──────┘  └──────┬──────┘
          └───────┘                                  │ job complete/failed
                                                     ▼
                                              ┌─────────────┐
                                              │ EventBridge │
                                              └──────┬──────┘
                                                     │ trigger
                                                     ▼
                                             ┌──────────────┐
                                             │ batch_status │
                                             │    lambda    │
                                             └──────┬───────┘
                                                    │ update status files
                                                    ▼
                                             ┌─────────────┐
                                             │ S3 (dest    │
                                             │  bucket)    │
                                             └─────────────┘
```

---

## 2. Authentication Flow

```
LOGIN
─────────────────────────────────────────────────────────────────────────────

  Browser             API Gateway          login λ           localStorage
     │                     │                  │                    │
     │  POST /v1/login      │                  │                    │
     │  {username,password} │                  │                    │
     ├────────────────────►│                  │                    │
     │                     │  invoke          │                    │
     │                     │  (no authorizer) │                    │
     │                     ├─────────────────►│                    │
     │                     │                  │ secrets.compare_   │
     │                     │                  │ digest(input,env)  │
     │                     │                  │                    │
     │                     │                  │ build JWT          │
     │                     │                  │ {sub,iat,exp=+24h} │
     │                     │                  │ sign HMAC-SHA256   │
     │                     │  200 {token}     │                    │
     │                     │◄─────────────────┤                    │
     │  200 {token}        │                  │                    │
     │◄────────────────────┤                  │                    │
     │                     │                  │                    │
     │  setItem(token) ────────────────────────────────────────────►
     │  redirect → aws-connect.html           │                    │


SUBSEQUENT PROTECTED REQUEST
─────────────────────────────────────────────────────────────────────────────

  Browser          API Gateway        authorizer λ      protected λ
     │                  │                  │                 │
     │  getItem(token)  │                  │                 │
     │◄──── localStorage│                  │                 │
     │                  │                  │                 │
     │  isTokenValid()  │                  │                 │
     │  check exp claim │                  │                 │
     │                  │                  │                 │
     │  POST /v1/aws-connect/...           │                 │
     │  Authorization: Bearer eyJ...       │                 │
     ├─────────────────►│                  │                 │
     │                  │  invoke          │                 │
     │                  │  authorizer      │                 │
     │                  ├─────────────────►│                 │
     │                  │                  │ re-compute HMAC │
     │                  │                  │ compare_digest()│
     │                  │                  │ check exp       │
     │                  │                  │                 │
     │       ┌──────────┴──────────────────┤                 │
     │       │ Token valid                 │                 │
     │       │  IAM Allow policy           │                 │
     │       │  + {username} in context    │                 │
     │       │                 ◄───────────┤                 │
     │       │  invoke handler             │                 │
     │       │  requestContext.authorizer  │                 │
     │       │  .username = "..."          │                 │
     │       │                 ────────────────────────────►│
     │       │  200 response               │                 │
     │◄──────┘                 ◄────────────────────────────┤
     │       │                             │                 │
     │       └──── Token invalid/expired ──┤                 │
     │             401 Unauthorized        │                 │
     │◄─────────────────┤                  │                 │
     │  removeItem(token)                  │                 │
     │  redirect → login.html              │                 │
```

---

## 3. Test Connection Flow

```
  POST /v1/aws-connect/test
           │
           ▼
  ┌─────────────────┐
  │  accessMethod?  │
  └────────┬────────┘
           │
     ┌─────┴──────────────────────┐
     │ assumeRole                 │ directIAM
     ▼                            ▼
  ┌──────────────────┐    ┌────────────────────────────┐
  │ CHECK 1          │    │ Build S3 client from        │
  │ STS.assume_role  │    │ accessKeyId + secretKey     │
  │ (Role A ARN)     │    └──────────────┬─────────────┘
  └───────┬──────────┘                   │
          │                              │
    ✗ ClientError                        │
    │                                    │
    ▼                                    │
  Return 403                             │
  Check 1 ✗                             │
  Checks 2-4 → SKIPPED                  │
                                         │
    ✓ Temp creds obtained                │
    │                                    │
    └──────────────┬─────────────────────┘
                   ▼
         ┌───────────────────┐
         │ CHECK 2           │
         │ S3.head_bucket    │
         │ (bucketName)      │
         └────────┬──────────┘
                  │
      ✗ 403 AccessDenied / 404 NotFound
      │
      ▼
    Return 403
    Check 2 ✗
    Checks 3-4 → SKIPPED

      ✓ Bucket exists and reachable
      │
      ▼
    ┌──────────────────────────┐
    │ CHECK 3                  │
    │ S3.get_bucket_versioning │
    └────────────┬─────────────┘
                 │
     ✗ Status = Suspended / empty
     │
     ▼
   Return 400
   Check 3 ✗
   Check 4 → SKIPPED

     ✓ Status = Enabled
     │
     ▼
   ┌──────────────────────────┐
   │ CHECK 4                  │
   │ S3.list_objects_v2       │
   │ MaxKeys=1                │
   └───────────┬──────────────┘
               │
   ✗ ClientError
   │
   ▼
 Return 403, Check 4 ✗

   ✓ Objects listed successfully
   │
   ▼
 Return 200 — all checks passed
```

---

## 4. Submit Connector Flow (5 Steps)

```
  POST /v1/aws-connect/submit
           │
           ▼
  Parse body: companyName, accountId, bucketName,
  region, accessMethod, roleArn, roleBArn, keys
           │
           ▼
  Derive: destBucket = "{company}-billing-data"
          clientId   = uuid4()
           │
           │
╔══════════╧══════════════════════════════════════════╗
║  STEP 1 — Create Destination Bucket                 ║
║                                                     ║
║  S3.create_bucket  "{company}-billing-data"         ║
║  S3.put_bucket_versioning          → Enabled        ║
║  S3.put_public_access_block        → full block     ║
║  S3.put_bucket_encryption          → AES-256        ║
║  S3.put_bucket_ownership_controls  → BucketOwner    ║
║  S3.put_object × 4                                  ║
║    _metadata/.keep  _batch/.keep                    ║
║    _replication/.keep  data/.keep                   ║
╚══════════╤══════════════════════════════════════════╝
           │
╔══════════╧══════════════════════════════════════════╗
║  STEP 2 — Write connector.json                      ║
║                                                     ║
║  accessMethod == "directIAM"?                       ║
║    YES → SecretsManager.create_secret               ║
║            /finomate/clients/{co}/aws-credentials   ║
║    NO  → skip Secrets Manager                       ║
║                                                     ║
║  S3.put_object  _metadata/connector.json            ║
║    { clientId, company, email, sourceBucket,        ║
║      destBucket, accessMethod, roleArns,            ║
║      secretsPath, batchJobStatus: "pending",        ║
║      replicationStatus: "pending",                  ║
║      createdAt, connectedBy }                       ║
╚══════════╤══════════════════════════════════════════╝
           │
╔══════════╧══════════════════════════════════════════╗
║  STEP 3 — Attach Bucket Policy to destBucket        ║
║                                                     ║
║  accessMethod == "assumeRole"?                      ║
║    YES → principal = Role B ARN                     ║
║    NO  → principal = arn:aws:iam::{accountId}:root  ║
║                                                     ║
║  S3.put_bucket_policy on destBucket                 ║
║    grants principal:                                ║
║      s3:ReplicateObject  s3:ReplicateDelete         ║
║      s3:ReplicateTags    s3:GetBucketVersioning      ║
║      s3:PutBucketVersioning  s3:List*               ║
╚══════════╤══════════════════════════════════════════╝
           │
╔══════════╧══════════════════════════════════════════╗
║  STEP 4 — Configure Live Replication                ║
║                                                     ║
║  assumeRole + roleArn + roleBArn present?           ║
║                                                     ║
║  NO  →  SKIP                                        ║
║         _replication/status.json                    ║
║         { status: "pending", manual setup note }    ║
║                                                     ║
║  YES →  STS.assume_role (Role A, client account)    ║
║         S3.put_bucket_replication on CLIENT bucket  ║
║           Rule: replicate all objects               ║
║           Destination: destBucket                   ║
║           Account: Finomate account                 ║
║           AccessControlTranslation: Destination     ║
║           Role: Role B                              ║
║         _replication/status.json                    ║
║         { status: "active", startedAt }             ║
╚══════════╤══════════════════════════════════════════╝
           │
╔══════════╧══════════════════════════════════════════╗
║  STEP 5 — Historical Batch Copy                     ║
║                                                     ║
║  Assume Role A again (or use direct keys)           ║
║  S3.list_objects_v2 paginated  ← all objects        ║
║                                                     ║
║  No objects found?                                  ║
║    → SKIP, _batch/status.json { status: "pending" } ║
║                                                     ║
║  Objects found:                                     ║
║    Build CSV manifest: bucket,"key" per line        ║
║    S3.put_object  _batch/manifest.csv               ║
║    S3Control.create_job (S3 Batch Operations)       ║
║      Operation: S3PutObjectCopy → data/             ║
║      Manifest: _batch/manifest.csv                  ║
║      ClientRequestToken: clientId  (idempotent)     ║
║    Update connector.json                            ║
║      batchJobId, batchJobStatus: "running"          ║
║    _batch/status.json                               ║
║      { status: "running", jobId, objectCount }      ║
╚══════════╤══════════════════════════════════════════╝
           │
           ▼
  Return 200
  { clientId, destinationBucket, steps: [...] }
```

---

## 5. Batch Status Update Flow (Async / EventBridge)

```
  S3 Batch Operations          EventBridge           batch_status λ            S3
         │                          │                       │                    │
         │  copying objects         │                       │                    │
         │  (minutes → hours)       │                       │                    │
         │                          │                       │                    │
         │  Job finishes            │                       │                    │
         ├─────────────────────────►│                       │                    │
         │  S3 Batch Operations     │                       │                    │
         │  Job State Change        │                       │                    │
         │  { jobId,                │                       │                    │
         │    status: Complete      │                       │                    │
         │            / Failed }    │                       │                    │
         │                          │  invoke               │                    │
         │                          ├──────────────────────►│                    │
         │                          │                       │ extract jobId      │
         │                          │                       │ + status           │
         │                          │                       │                    │
         │                          │                       │  list_buckets()   ─┼─►
         │                          │                       │◄──────────────────┼── all buckets
         │                          │                       │                    │
         │                          │          ┌────────────┤                    │
         │                          │          │ for each bucket                 │
         │                          │          │ ending in "-billing-data"       │
         │                          │          │            │                    │
         │                          │          │            │  get_object        │
         │                          │          │            │  _metadata/  ─────►│
         │                          │          │            │  connector.json    │
         │                          │          │            │◄───────────────────┤
         │                          │          │            │                    │
         │                          │          │  batchJobId == jobId?           │
         │                          │          │                                 │
         │                          │          │    NO  → continue to next bucket│
         │                          │          │                                 │
         │                          │          │    YES (match found):           │
         │                          │          │            │  put_object        │
         │                          │          │            │  _batch/status.json─┼─►
         │                          │          │            │  { status, jobId,   │
         │                          │          │            │    completedAt }    │
         │                          │          │            │  put_object        │
         │                          │          │            │  connector.json ───►│
         │                          │          │            │  batchJobStatus =  │
         │                          │          │            │  "complete"|"failed"│
         │                          │          │            │                    │
         │                          │          │            │  return (stop scan)│
         │                          │          └────────────┘                    │
```

---

## 6. S3 Destination Bucket Structure

```
  company-billing-data/
  │
  ├── _metadata/
  │   └── connector.json
  │         clientId          — UUID assigned at submit time
  │         companyName       — slugified company name
  │         billingEmail      — contact email
  │         awsAccountId      — client's AWS account ID
  │         sourceBucket      — client's CUR bucket name
  │         sourceRegion      — client's bucket region
  │         destinationBucket — this bucket
  │         accessMethod      — "assumeRole" | "directIAM"
  │         roleArn           — Role A ARN (assumeRole only)
  │         roleBArn          — Role B ARN (assumeRole only)
  │         secretsPath       — SM path (directIAM only)
  │         batchJobId        — S3 Batch Operations job ID
  │         batchJobStatus    — pending | running | complete | failed
  │         replicationStatus — pending | active | error
  │         createdAt         — ISO timestamp
  │         connectedBy       — portal username (from JWT)
  │
  ├── _batch/
  │   ├── manifest.csv        — bucket,"key" per source object (CSV)
  │   ├── status.json         — { status, jobId, objectCount, timestamps }
  │   └── reports/            — S3 Batch Operations completion report files
  │
  ├── _replication/
  │   └── status.json         — { status, startedAt } or { status, error }
  │
  └── data/
      └── ...                 — copied CUR objects (under data/ prefix)
```

---

## 7. IAM Role Trust Chain (AssumeRole method)

```
╔══════════════════════════════════════════╗    ╔══════════════════════════════════════════╗
║         FINOMATE AWS ACCOUNT             ║    ║         CLIENT AWS ACCOUNT               ║
║                                          ║    ║                                          ║
║  ┌───────────────────────────────────┐   ║    ║  ┌───────────────────────────────────┐   ║
║  │ Lambda Execution Role             │   ║    ║  │ Role A                            │   ║
║  │ finomate-lambda-execution-role    │   ║    ║  │ Trust policy: Finomate account    │   ║
║  │                                   │   ║    ║  │ Permissions:                      │   ║
║  │ Permissions:                      ├───╫────╫─►│   s3:GetBucketVersioning          │   ║
║  │   sts:AssumeRole                  │   ║    ║  │   s3:ListBucket                   │   ║
║  │   s3:CreateBucket                 │◄──╫────╫──┤   s3:PutBucketReplication         │   ║
║  │   iam:PassRole (→ BatchRole)      │   ║    ║  │   s3:GetObject  etc.              │   ║
║  └──────────────┬────────────────────┘   ║    ║  └───────────────────────────────────┘   ║
║                 │                        ║    ║                                          ║
║                 │ iam:PassRole           ║    ║  ┌───────────────────────────────────┐   ║
║                 ▼                        ║    ║  │ Role B                            │   ║
║  ┌───────────────────────────────────┐   ║    ║  │ Trust policy: s3.amazonaws.com    │   ║
║  │ Batch Operations Role             │   ║    ║  │ (replication service principal)   │   ║
║  │ finomate-batch-operations-role    │   ║    ║  │ Permissions:                      │   ║
║  │                                   │   ║    ║  │   s3:ReplicateObject              │   ║
║  │ Trust: batchoperations.s3         │   ║    ║  │   s3:GetObject                    │   ║
║  │ Permissions:                      │   ║    ║  │   s3:GetBucketVersioning  etc.    │   ║
║  │   s3:GetObject (source bucket)    │   ║    ║  └─────────────┬─────────────────────┘   ║
║  │   s3:PutObject (dest bucket)      │   ║    ║                │                         ║
║  └──────────────┬────────────────────┘   ║    ║  ┌─────────────▼─────────────────────┐   ║
║                 │                        ║    ║  │ client-cur-bucket (source)        │   ║
║                 │ reads from             ║    ║  │                                   │   ║
║                 │──────────────────────────────╫─┤ Replication rule:                 │   ║
║                 │                        ║    ║  │   destination → destBucket        │   ║
║  ┌──────────────▼────────────────────┐   ║    ║  │   role        → Role B            │   ║
║  │ company-billing-data (dest)       │   ║    ║  │   owner       → Destination       │   ║
║  │                                   │◄──╫────╫──┤                                   │   ║
║  │ Bucket policy grants Role B:      │   ║    ║  │ New objects replicate             │   ║
║  │   s3:ReplicateObject              │   ║    ║  │ automatically via Role B ─────────╫──►║
║  │   s3:ReplicateDelete etc.         │   ║    ║  └───────────────────────────────────┘   ║
║  └───────────────────────────────────┘   ║    ║                                          ║
╚══════════════════════════════════════════╝    ╚══════════════════════════════════════════╝

  Flow summary:
  1. Lambda assumes Role A (sts:AssumeRole)  →  gets temp creds scoped to client account
  2. Lambda uses Role A creds               →  writes replication rule on client bucket
  3. Client bucket replicates via Role B    →  new objects land in company-billing-data
  4. Batch Operations Role                  →  copies historical objects (one-time job)
```
