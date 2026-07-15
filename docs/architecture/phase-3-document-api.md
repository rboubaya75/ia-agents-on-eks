# Phase 3 — Document API

## Implemented vertical slice

```text
Authenticated client
    ↓ validated Cognito access token
FastAPI document endpoints
    ↓ trusted Principal tenant/user
DocumentManagementService
    ├── DynamoDB document and PENDING job metadata
    ├── temporary S3 presigned upload
    ├── conditional promotion to immutable source
    └── SQS ingestion task
              ↓
Document worker
    ↓ canonical job reload
DocumentIngestionService
    ├── UTF-8 TXT/Markdown extraction
    ├── paragraph chunking
    ├── Bedrock embeddings
    ├── S3 chunk storage
    └── fenced S3 Vectors publication
```

The API and ingestion worker are two entrypoints of the same immutable application image. The API does not execute extraction, Bedrock calls or vector publication.

## Endpoints

```text
POST   /api/v1/documents
POST   /api/v1/documents/{documentId}/upload-url
GET    /api/v1/documents/{documentId}
POST   /api/v1/documents/{documentId}/ingestions
GET    /api/v1/documents/{documentId}/ingestions/{jobId}
```

The ingestion endpoint requires `Idempotency-Key` and returns `202 Accepted` with a canonical `PENDING` job. Request schemas forbid unknown fields, so client-supplied tenant, model alias and pipeline version values are rejected.

Document deletion is intentionally not exposed in this increment. A durable deletion API requires a transactional request/outbox, a dedicated worker, renewable ownership and fenced tombstone completion; it will be delivered through a separate ADR and pull request.

## Source upload and promotion

Document registration records title, checksum, MIME type, language, classification and allowed roles. `sourceVersion` is the registered SHA-256 checksum.

The upload URL targets a temporary session key and signs:

```text
PUT method
generated temporary key
Content-Type
Content-Length
x-amz-checksum-sha256
temporary-upload tags
server-side encryption headers
expiration between 60 and 900 seconds
```

Temporary keys are scoped by tenant, document and server-generated upload session. A mandatory one-day lifecycle rule covers the complete temporary prefix and removes abandoned uploads.

When the client submits ingestion, the service holds the document-version lease, validates the temporary object with `HeadObject`, and copies it conditionally to the immutable source key using the observed ETag. The worker reads only the immutable source.

## Asynchronous submission

```text
POST ingestion + Idempotency-Key
    ↓
derive canonical jobId from tenant/document/key
    ↓
acquire document-version lease
    ↓
promote source when PENDING_UPLOAD
    ↓
conditional DynamoDB job submit as PENDING
    ↓
enqueue typed SQS task
    ↓
202 Accepted
```

Embedding alias, embedding profile revision, resolved model ID and pipeline version are server configuration. They are not accepted from the client.

The worker entrypoint is:

```text
python -m ia_backend_api.document_worker
```

It long-polls SQS, reloads the canonical job, skips terminal jobs, executes pending jobs through the existing ingestion service and acknowledges only terminal outcomes. Busy leases and unexpected infrastructure failures remain unacknowledged for SQS retry.

## Authorization

Document write operations require:

```text
platform/documents.write
AND tenant-admin or platform-admin
```

Every operation on an existing document also enforces the document classification and role ACL. Read and mutation denials return the same `404 document_not_found` response to avoid existence or classification disclosure.

Tenant context is never accepted from bodies, query parameters, SQS messages supplied by a client or S3 metadata.

## Supported formats

This increment supports:

- `text/plain`;
- `text/markdown`.

Strict UTF-8 decoding, line-ending normalization, content limits, checksum recomputation and control-character rejection are applied before chunking. PDF and DOCX are not silently treated as text.

## Feature flag and configuration

`IA_DOCUMENT_API_ENABLED` defaults to `false`. Existing Phase 2 installations therefore start without document-specific settings or AWS resources.

When enabled, validation requires:

- DynamoDB control table;
- private document bucket;
- temporary-upload lifecycle rule ID;
- SQS ingestion queue URL and visibility timeout;
- source and index prefixes;
- optional KMS key identifier;
- maximum source size;
- S3 Vectors bucket and index;
- immutable Bedrock embedding profile;
- server pipeline version.

No AWS account ID, ARN, bucket name, table name, model ID or secret is embedded in application code.

## Readiness

When the feature is enabled, readiness is fail-closed over:

- chat-session DynamoDB;
- document control DynamoDB;
- document S3 bucket and temporary lifecycle rule;
- SQS ingestion queue;
- S3 Vectors index;
- local resolution of the configured embedding profile.

The readiness path never invokes an embedding model.

## Deferred

- durable document deletion with transactional outbox, dedicated queue and worker, lease heartbeat and fenced completion;
- Terraform, IAM and Helm resources required to enable the feature;
- PDF and DOCX parser isolation;
- frontend document-management screens;
- pending-job reconciliation and redispatch operations;
- retrieval, citation rendering and custom agents;
- audit-record retention and purge policy.
