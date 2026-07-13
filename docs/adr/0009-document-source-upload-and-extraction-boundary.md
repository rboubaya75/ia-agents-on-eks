# ADR-0009: Document source upload and extraction boundary

- Status: Accepted
- Date: 2026-07-13

## Context

The versioned ingestion core and AWS storage adapters are merged, but authenticated users cannot yet register source documents, upload them or trigger ingestion through the platform API. Source files may contain sensitive tenant data and must not transit through application memory during upload or use client-controlled filenames as storage keys.

The first API increment must remain small enough to validate the complete security boundary before introducing complex PDF and DOCX parsers.

## Decision

### Direct S3 upload

The backend registers immutable source metadata and returns a short-lived presigned S3 `PutObject` URL. The browser uploads directly to a private S3 bucket.

The signed request binds:

- the tenant-scoped generated object key;
- content type;
- exact content length;
- SHA-256 checksum;
- SSE-S3 or configured SSE-KMS headers;
- a maximum lifetime of 15 minutes.

Client filenames are never used in object keys. Logical source keys are:

```text
<configured-prefix>/sources/{tenantId}/{documentId}/{sourceVersion}/original
```

Every component is base64url encoded by the adapter.

### Trusted tenant context

No document endpoint accepts `tenantId`. The API derives tenant and user identities exclusively from the validated Cognito `Principal`. Write operations require the configured document scope and either `tenant-admin` or `platform-admin`.

Requested classification cannot exceed the principal's trusted maximum classification. Reads additionally require an allowed document role or an administrator role.

### Initial extraction boundary

Only `text/plain` and `text/markdown` are accepted in this increment. Extraction:

- performs a bounded S3 read;
- recomputes SHA-256 over the retrieved bytes;
- requires strict UTF-8;
- normalizes line endings;
- rejects NUL, DEL and unsupported control characters;
- rejects empty documents;
- never logs document content.

PDF and DOCX remain deferred until their parser isolation, archive limits and malicious-file controls are designed separately.

### Lifecycle

A document is registered as `PENDING_UPLOAD`. Before ingestion, S3 metadata must match the registered content type and checksum and remain below the configured maximum size. The service then advances the document to `UPLOADED` with optimistic locking and delegates to `DocumentIngestionService`.

Deletion first advances the document to `DELETING`, then removes source objects, chunks and vectors. Any partial failure leaves the recoverable `DELETING` state and returns an error. Successful cleanup writes the `DELETED` tombstone. Historical job and generation metadata is retained for audit and is no longer reachable through document APIs after the tombstone.

## Consequences

- API instances do not proxy source upload bytes.
- Bucket names, prefixes, KMS identifiers, size limits, model identifiers and vector indexes remain deployment configuration.
- Source object validation is performed before indexing and again by the extractor over actual bytes.
- Tenant isolation does not rely on request payload values or client filenames.
- TXT and Markdown provide a controlled vertical slice before complex parser dependencies are accepted.
- Purging retained control-plane audit records requires a later retention-policy increment.
