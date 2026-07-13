# Phase 3 — AWS document adapters

## Implemented increment

This increment connects the versioned ingestion core to AWS-specific adapters:

```text
DocumentIngestionService
├── DynamoDB control repositories
├── Bedrock Titan embedding provider
├── S3 generation-scoped chunk store
├── S3 vector-key manifests
└── S3 Vectors repository
```

No domain or application module imports boto3.

## DynamoDB single-table access patterns

The configurable control table uses:

```text
PK = length-prefixed TENANT + tenantId
SK = one of:
  DOCUMENT + documentId
  JOB + jobId
  FINGERPRINT + fingerprint
  LEASE + documentId + sourceVersion
  GENERATION + documentId + generationId
```

Length-prefix encoding avoids separator collisions. Every decoded entity is revalidated against the trusted tenant and requested identity.

### Fenced job state

Claiming a fingerprint creates the running job and the canonical fingerprint marker atomically. Later terminal-state writes also update those two items in one transaction.

A terminal write is accepted only when the stored job and marker still have the same:

```text
jobId
fingerprint
fencingToken
```

A worker holding an older fencing token cannot overwrite a newer retry, even when both attempts reuse the same job ID.

### Atomic publication

Activation uses a single DynamoDB transaction:

1. condition-check the current document-version lease;
2. conditionally advance the document revision and active-generation pointer;
3. conditionally move the ready generation to active;
4. conditionally move the running job to succeeded;
5. conditionally mark the canonical fingerprint succeeded.

The lease check requires:

```text
expected tenant and document
expected source version
ownerToken == publishing jobId
fencingToken == candidate fencing token
expiresAt > activation timestamp
```

The remaining conditions verify source version, document revision, fingerprint and fencing token. A stale or expired worker therefore cannot publish after a newer lease has been issued.

### Ambiguous transaction recovery

A network or infrastructure error does not prove that `TransactWriteItems` failed. After a non-conditional activation exception, the adapter performs strongly consistent reads of:

```text
document
generation
ingestion job
fingerprint marker
```

The operation is treated as successful only when all four records expose the exact committed generation, fingerprint, fencing token and terminal states. Candidate cleanup remains allowed only when this reconciliation does not show the atomic commit.

## Candidate object layout

All path components are encoded, but the logical structure is:

```text
<configured-prefix>/chunks/{tenantId}/{generationId}/{chunkId}
<configured-prefix>/chunk-manifests/{tenantId}/{documentId}/{generationId}
<configured-prefix>/vector-manifests/{tenantId}/{documentId}/{generationId}
```

The chunk manifest is written before any chunks. Vector keys are recorded before the corresponding S3 Vectors batch. Failed candidate cleanup is therefore generation-scoped and does not inspect or delete the active generation.

## Bedrock embedding profile

A profile is immutable and configured as:

```text
alias
revision
modelId
dimensions: 256 | 512 | 1024
normalize
```

The adapter applies bounded concurrency and configured botocore timeouts/retries. It rejects blank input, oversized input, malformed JSON, dimension mismatches, non-numeric values and non-finite values.

## S3 Vectors metadata and retrieval boundary

Each vector stores:

```text
tenantId
documentId
chunkId
generationId
classification
allowedRoles
sourceVersion
checksum
embeddingModelId
embeddingDimensions
pipelineVersion
```

A production query must include authoritative active generation IDs. The S3 Vectors filter combines:

```text
tenantId
classification set
role set
active generation set
```

The adapter revalidates all returned metadata before constructing matches.

## Configuration and security

Required values are supplied by deployment configuration:

- DynamoDB control-table name;
- S3 bucket and object prefix;
- optional KMS key ID;
- S3 Vectors bucket/index names or index ARN;
- Bedrock region and immutable embedding profiles.

No secret, account ID, ARN, resource name or model ID is committed as an environment-specific constant. IAM must grant only the operations used by each adapter.

## Validation scope

Unit and contract tests cover:

- DynamoDB type serialization and pagination-independent control operations;
- conditional versus transient DynamoDB failures;
- transaction idempotency tokens;
- optimistic document revisions;
- atomic fingerprint claims and fenced terminal-state writes;
- active, owned and unexpired lease checks during five-item activation;
- reconciliation after an ambiguous but committed activation;
- propagation of ambiguous errors when no commit is visible;
- fenced lease acquisition;
- S3 chunk round trips and rollback manifests;
- KMS encryption parameters;
- Bedrock request/response validation;
- vector-manifest-before-write ordering;
- exact vector cleanup;
- tenant, classification, role and active-generation filtering.

## Deferred

- source-file upload and presigned URLs;
- PDF, DOCX, Markdown and text extractors;
- document/reindex/delete API endpoints;
- Terraform and IAM resources;
- real AWS integration tests;
- retriever orchestration, authorization revalidation and citation generation.
