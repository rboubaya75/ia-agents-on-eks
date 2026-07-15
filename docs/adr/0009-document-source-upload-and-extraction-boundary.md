# ADR-0009: Document source upload and extraction boundary

- Status: Accepted
- Date: 2026-07-13
- Updated: 2026-07-15

## Context

The versioned ingestion core and AWS storage adapters are merged, but authenticated users cannot yet register source documents, upload them or trigger ingestion through the platform API. Source files may contain sensitive tenant data and must not transit through application memory during upload or use client-controlled filenames as storage keys.

A presigned URL pointing directly at the immutable source key creates a future retention race: once durable deletion exists, a client could execute an already issued PUT after cleanup and recreate an object that is no longer reachable by the deletion workflow.

The first API increment must remain small enough to validate the complete security boundary before introducing complex PDF and DOCX parsers or a durable deletion workflow.

## Decision

### Temporary direct S3 upload

The backend registers immutable source metadata and returns a short-lived presigned S3 `PutObject` URL. The browser uploads directly to a private, tenant-scoped temporary key:

```text
<configured-prefix>/uploads/{tenantId}/{documentId}/{uploadSessionId}/original
```

Every key component is base64url encoded by the adapter. Client filenames are never used.

The signed request binds:

- the generated temporary key;
- content type;
- exact content length;
- SHA-256 checksum;
- temporary-upload tags;
- SSE-S3 or configured SSE-KMS headers;
- a maximum lifetime of 15 minutes.

Temporary upload objects are covered by a required, enabled S3 lifecycle rule expiring the upload prefix after one day. The document runtime readiness probe verifies that rule before the API is considered ready.

### Promotion to immutable source

The temporary object is never read by the ingestion engine. During idempotent ingestion submission, while holding the document-version lease, the application:

1. reads temporary object metadata with checksum mode enabled;
2. validates MIME type, exact checksum and maximum size;
3. requires the current object ETag;
4. copies the object conditionally to the immutable source key with `CopySourceIfMatch`;
5. replaces the temporary tag and applies configured encryption;
6. deletes the temporary object;
7. re-reads the final source metadata before queuing ingestion.

The immutable source key is:

```text
<configured-prefix>/sources/{tenantId}/{documentId}/{sourceVersion}/original
```

A late PUT can therefore create only a temporary object. It cannot recreate an immutable source and is removed by the lifecycle policy. This storage boundary is intentionally established before the future durable deletion workflow is introduced.

### Trusted tenant and authorization context

No document endpoint accepts `tenantId`. The API derives tenant and user identities exclusively from the validated Cognito `Principal`.

Write operations require the configured document scope and either `tenant-admin` or `platform-admin`. Every mutation of an existing document also verifies:

- document tenant through the repository key;
- document classification against the trusted maximum classification;
- document role authorization.

Denied reads and mutations return a uniform not-found response to avoid revealing document existence or classification.

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

### Deletion scope

Document deletion is not part of this increment. No public deletion endpoint, application command, management-port operation or synchronous cleanup workflow is delivered.

Durable deletion requires a separate ADR and implementation covering a transactional request/outbox, a dedicated encrypted queue and DLQ, renewable execution ownership, lease and visibility heartbeat, fenced tombstone completion, retries, reconciliation and audit-retention policy.

The document states `DELETING` and `DELETED` remain reserved in the domain model so the future workflow can be introduced without redefining persisted status values. This reservation does not constitute an executable deletion capability.

## Consequences

- API instances do not proxy source upload bytes.
- Late presigned PUTs cannot create immutable source objects.
- The deployment must configure and retain the one-day temporary-upload lifecycle rule before enabling the feature.
- Bucket names, prefixes, KMS identifiers, size limits, model identifiers and vector indexes remain deployment configuration.
- Source object validation is performed before queuing and again by the extractor over actual bytes.
- Tenant isolation does not rely on request payload values or client filenames.
- TXT and Markdown provide a controlled vertical slice before complex parser dependencies are accepted.
- Durable deletion and control-plane retention remain explicitly deferred to a separately reviewed increment.
