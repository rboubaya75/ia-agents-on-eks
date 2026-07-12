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

### Atomic publication

Activation uses a single DynamoDB transaction:

1. conditionally advance the document revision and active-generation pointer;
2. conditionally move the ready generation to active;
3. conditionally move the running job to succeeded;
4. conditionally mark the canonical fingerprint succeeded.

Conditions verify source version, document revision, fingerprint and fencing token. A stable transaction token makes an exact retry idempotent.

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
- atomic fingerprint claims and four-item activation;
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
