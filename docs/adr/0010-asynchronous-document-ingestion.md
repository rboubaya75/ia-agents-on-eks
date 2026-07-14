# ADR-0010: Asynchronous and recoverable document ingestion

- Status: Accepted
- Date: 2026-07-14

## Context

Document ingestion performs source extraction, chunking, multiple Bedrock embedding calls, S3 chunk writes, S3 Vectors publication and a fenced DynamoDB activation transaction. Executing that pipeline inside an HTTP request creates unstable latency, reverse-proxy timeouts and client retries that can amplify model cost.

The public API must also prevent callers from selecting embedding profiles or inventing pipeline versions to force distinct fingerprints and repeated indexing work. The worker must survive at-least-once SQS delivery, process crashes, transient AWS failures, long-running embedding workloads and malformed messages without weakening deletion fencing.

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

### Recoverable worker execution

The same immutable application image exposes a separate worker entrypoint:

```text
python -m ia_backend_api.document_worker
```

The worker long-polls SQS, reloads the canonical job and may execute `PENDING`, recoverable `FAILED`, or redelivered `RUNNING` jobs. A `RUNNING` job is retried only through the existing document-version lease and fingerprint claim. After the previous lease expires, the replacement receives a higher fencing token; stale activation remains impossible.

Terminal or missing jobs are acknowledged without re-execution. Validation and deterministic content failures are persisted and acknowledged. Unexpected infrastructure failures are persisted with the retryable code `INGESTION_FAILED`, left unacknowledged and may be reclaimed with a higher fencing token.

When a successful fingerprint already exists under another job ID, the submitted job becomes a result alias and copies the canonical generation ID, counters, checksums and resolved embedding metadata. It never reports `SUCCEEDED` without a generation reference.

### Heartbeat and visibility

The worker execution lease TTL, heartbeat interval and SQS visibility timeout are server configuration. Startup validation requires:

```text
heartbeat interval < lease TTL <= SQS visibility timeout
```

While ingestion runs, the worker periodically:

1. conditionally renews the DynamoDB lease for the current owner;
2. extends the SQS message visibility timeout.

Loss of the lease or inability to extend visibility is fail-closed. The worker does not acknowledge the message, and activation remains protected by the fencing token and authoritative lease check.

### Poison messages

SQS messages are schema-validated before entering the application worker. Invalid JSON, missing fields and unknown fields are not acknowledged and do not terminate the process. Their receipt and `ApproximateReceiveCount` remain controlled by SQS so the configured redrive policy can move them to the dead-letter queue.

The outer worker loop isolates unexpected iteration errors while allowing `CancelledError` to stop the process cleanly.

### Shared fencing with deletion

Submission, ingestion and deletion use the same tenant/document/source-version lease. Deletion cannot start while an ingestion worker owns an unexpired lease. Activation remains protected by its fencing token and authoritative lease condition.

A failed worker restores document state only from `PROCESSING`; it never overwrites `DELETING` after lease expiry or takeover.

### Feature gating and compatibility

The document runtime is disabled by default through `IA_DOCUMENT_API_ENABLED=false`. All document-specific settings remain optional while disabled, preserving Phase 2 startup compatibility.

When enabled, settings validation requires the DynamoDB control table, private S3 bucket, temporary-upload lifecycle rule ID, SQS queue URL, S3 Vectors index, immutable embedding profile and pipeline version, plus coherent worker timing.

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
- Worker crashes and expired leases are recoverable through higher fencing tokens.
- Transient AWS failures are retried by SQS instead of being silently terminalized.
- Long-running ingestions keep both lease and visibility ownership alive.
- Poison messages rely on the queue redrive policy and do not crash the worker process.
- SQS, worker deployment, IAM, lifecycle and dead-letter configuration are required before the feature flag can be enabled.
- At-least-once delivery is safe because job submission, fingerprint claim, leases and activation are idempotent or fenced.
- Pending jobs after ambiguous dispatch require an operational reconciliation mechanism; this can be a scheduled redispatcher in a later infrastructure increment.
