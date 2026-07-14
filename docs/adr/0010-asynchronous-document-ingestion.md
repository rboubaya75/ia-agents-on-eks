# ADR-0010: Asynchronous document ingestion submission

- Status: Accepted
- Date: 2026-07-14

## Context

Document ingestion performs source extraction, chunking, multiple Bedrock embedding calls, S3 chunk writes, S3 Vectors publication and a fenced DynamoDB activation transaction. Executing that pipeline inside an HTTP request creates unstable latency, reverse-proxy timeouts and client retries that can amplify model cost.

The public API must also prevent callers from selecting embedding profiles or inventing pipeline versions to force distinct fingerprints and repeated indexing work.

The document infrastructure is not yet deployed by Terraform and Helm. Introducing mandatory runtime settings unconditionally would break existing Phase 2 installations before the infrastructure increment is approved.

## Decision

### Server-owned ingestion identity

The public ingestion request contains no embedding alias or pipeline version. Those values are immutable deployment configuration and are persisted on the submitted job.

The client supplies an `Idempotency-Key` header. The API derives the canonical job identifier from:

```text
tenantId + documentId + Idempotency-Key
```

Tenant identity comes only from the validated Cognito principal. Reusing the same key for the same document returns the canonical persisted job.

### Persist then enqueue

The API acquires the document-version lease, promotes and validates the temporary upload when required, and conditionally persists an `IngestionJob` in `PENDING` state. It then sends a typed task containing only tenant, document and job identifiers to SQS and returns `202 Accepted`.

The queue may be standard or FIFO. For FIFO queues:

- message group is tenant plus document;
- deduplication identifier is the canonical job ID.

A queue send failure is reported as an incomplete operation while the persisted pending job remains available for reconciliation or redispatch. The API never performs Bedrock calls.

### Worker execution

The same immutable application image exposes a separate worker entrypoint:

```text
python -m ia_backend_api.document_worker
```

The worker long-polls SQS, reloads the canonical job, and executes only jobs still in `PENDING`. It reconstructs `IngestDocumentCommand` from server-owned job metadata and delegates to the existing fenced `DocumentIngestionService`.

Terminal or missing jobs are acknowledged without re-execution. A busy document lease or an unexpected infrastructure failure leaves the message unacknowledged for retry. Expected terminal ingestion failures are persisted and acknowledged.

### Shared fencing with deletion

Submission, ingestion and deletion use the same tenant/document/source-version lease. Deletion cannot start while an ingestion worker owns an unexpired lease. Activation remains protected by its fencing token and authoritative lease condition.

A failed worker restores document state only from `PROCESSING`; it never overwrites `DELETING` after lease expiry or takeover.

### Feature gating and compatibility

The document runtime is disabled by default through `IA_DOCUMENT_API_ENABLED=false`. All document-specific settings remain optional while disabled, preserving Phase 2 startup compatibility.

When enabled, settings validation requires the DynamoDB control table, private S3 bucket, temporary-upload lifecycle rule ID, SQS queue URL, S3 Vectors index, immutable embedding profile and pipeline version.

The API and worker use the same runtime factory. The API exposes document routes but returns service unavailable when the feature is disabled.

### Readiness

When documents are enabled, readiness is composite and fail-closed over:

- chat-session DynamoDB table;
- document control DynamoDB table;
- private document S3 bucket and temporary-upload lifecycle rule;
- SQS ingestion queue;
- S3 Vectors index;
- configured embedding profile resolution.

No readiness probe invokes the embedding model.

## Consequences

- HTTP latency is bounded to authorization, promotion, persistence and enqueueing.
- Bedrock cost cannot be multiplied by arbitrary client pipeline versions or model aliases.
- The ingestion-status endpoint represents real asynchronous progress.
- SQS, worker deployment, IAM and lifecycle configuration are required before the feature flag can be enabled.
- At-least-once delivery is safe because job submission, fingerprint claim, leases and activation are idempotent or fenced.
- Pending jobs after ambiguous dispatch require an operational reconciliation mechanism; this can be a scheduled redispatcher in a later infrastructure increment.
