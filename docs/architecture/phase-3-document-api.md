# Phase 3 — Document API

## Implemented vertical slice

```text
Authenticated client
    ↓ Cognito access token
FastAPI document endpoints
    ↓ trusted Principal tenant/user
DocumentManagementService
    ├── DynamoDB document metadata
    ├── S3 presigned source upload
    ├── S3 metadata validation
    ├── DocumentIngestionService
    │   ├── UTF-8 TXT/Markdown extraction
    │   ├── paragraph chunking
    │   ├── Bedrock embeddings
    │   └── S3 Vectors publication
    └── source/chunk/vector deletion
```

## Endpoints

```text
POST   /api/v1/documents
POST   /api/v1/documents/{documentId}/upload-url
GET    /api/v1/documents/{documentId}
POST   /api/v1/documents/{documentId}/ingestions
GET    /api/v1/documents/{documentId}/ingestions/{jobId}
DELETE /api/v1/documents/{documentId}
```

Request schemas forbid unknown fields, so a client-supplied `tenantId` is rejected with validation status 422. The path identifiers are always combined with the tenant from the verified token before repository access.

## Source upload contract

Document registration records title, checksum, MIME type, language, classification and allowed roles. `sourceVersion` is the registered SHA-256 checksum for this immutable source version.

The upload URL signs:

```text
PUT method
private bucket and generated key
Content-Type
Content-Length
x-amz-checksum-sha256
server-side encryption headers
expiration between 60 and 900 seconds
```

After upload, `HeadObject` with checksum mode enabled validates metadata. The extractor uses a bounded `GetObject` range and recomputes the checksum over the actual bytes.

## Authorization

Write operations require:

```text
platform/documents.write
AND tenant-admin or platform-admin
```

Read operations require `platform/documents.read`, a classification no higher than the principal's maximum, and either an allowed document role or an administrator role.

Tenant context is never accepted from bodies, query parameters or S3 metadata.

## Supported formats

This increment supports:

- `text/plain`;
- `text/markdown`.

Strict UTF-8 decoding, line-ending normalization, content limits and control-character rejection are applied before chunking. PDF and DOCX are not silently treated as text.

## Deletion semantics

```text
current state
    ↓ optimistic transition
DELETING
    ├── delete all source versions
    ├── delete all chunk generations
    └── delete all vector generations
         ↓ all successful
DELETED tombstone
```

A partial cleanup does not report success. The document remains `DELETING` for explicit recovery. Control-plane job and generation records are retained for audit and are hidden by the tombstone.

## Configuration

All deployment-specific values are environment configuration:

- DynamoDB control table;
- private document bucket;
- source and index prefixes;
- optional KMS key identifier;
- maximum source size;
- S3 Vectors bucket and index;
- immutable Bedrock embedding profile.

No AWS account ID, ARN, bucket name, table name, model ID or secret is embedded in application code.

## Deferred

- PDF and DOCX parser isolation;
- asynchronous queue-based ingestion;
- Terraform, IAM and Helm resources;
- frontend document-management screens;
- retrieval, citation rendering and custom agents;
- audit-record retention and purge policy.
