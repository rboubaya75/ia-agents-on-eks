# ADR-0011: Durable asynchronous document deletion

- Status: Accepted
- Date: 2026-07-15

## Context

Deleting a document requires deleting its immutable source and temporary uploads from S3, every generation of stored chunks, every generation of S3 Vectors records and their manifests, then persisting a `DELETED` tombstone.

Running that workflow inside the HTTP request has several undesirable properties:

- cleanup time grows with the number of prior index generations;
- ALB, ingress or client timeouts can interrupt the request while cleanup continues remotely;
- the API pod requires destructive S3 and S3 Vectors permissions;
- retries after partial completion are difficult to operate and observe;
- a transient AWS failure leaves the caller without a durable retry mechanism.

Deletion must preserve the same tenant, document-version lease and fencing model used by ingestion. It must also remain disabled until its infrastructure and IAM are explicitly delivered.

## Decision

### API responsibility

The authenticated API remains responsible for authorization and disclosure controls. It derives tenant context only from the validated Cognito principal and reloads the document before accepting a deletion.

The API then:

1. acquires the shared tenant/document/source-version lease;
2. conditionally changes the document state to `DELETING`;
3. publishes a typed deletion task to a dedicated SQS queue;
4. releases the short submission lease;
5. returns without deleting S3 or S3 Vectors data.

Persisting `DELETING` before dispatch makes an ambiguous or failed queue send visible and recoverable. Repeating the operation is idempotent and may redispatch the same logical deletion.

### Dedicated deletion queue

Document deletion uses a queue distinct from document ingestion. A deletion task contains only:

```text
tenantId
documentId
operationId
```

The queue must have:

- server-side encryption through SSE-SQS or KMS;
- a dead-letter queue and valid redrive policy;
- a bounded positive `maxReceiveCount`;
- a queue type consistent with its configured URL.

For FIFO queues, the message group is tenant plus document and the deduplication identifier is the deletion operation ID.

Readiness fails closed when these operational properties are missing.

### Worker responsibility

A separate entrypoint executes deletion tasks:

```text
python -m ia_backend_api.document_deletion_worker
```

The worker:

1. reloads the trusted tenant-scoped document;
2. requires the state to be `DELETING`;
3. acquires the shared document-version lease;
4. deletes source objects, chunk generations and vector generations;
5. conditionally persists the `DELETED` tombstone;
6. acknowledges the message only after the tombstone is durable.

Partial cleanup is safe to retry because all deletion adapters are document- or generation-scoped and delete operations are idempotent. A transient cleanup or persistence failure leaves the message unacknowledged and the document in `DELETING`.

### IAM separation

The API role requires permission to read/update document metadata and send to the deletion queue. It does not require S3 object deletion or S3 Vectors deletion permissions.

The deletion worker role receives only the destructive permissions needed for tenant-scoped document cleanup, queue consumption and lease/tombstone updates.

### Observability

Worker entrypoints emit structured lifecycle and error events without logging document content, presigned URLs, credentials, tokens, prompts or provider error messages. Deployment metrics and alerts must cover queue age, retries, DLQ depth, cleanup duration and documents remaining in `DELETING`.

## Consequences

- HTTP deletion latency no longer depends on document size or generation count.
- Cleanup survives process restarts and transient AWS failures.
- Destructive permissions can be removed from API pods.
- A dedicated worker deployment, encrypted SQS queue, DLQ, least-privilege IAM and reconciliation alerting are required before enabling the document feature.
- The public document remains unavailable while `DELETING`; final deletion is represented by the durable tombstone.
- At-least-once delivery is handled through idempotent cleanup, conditional metadata writes and the shared lease.
