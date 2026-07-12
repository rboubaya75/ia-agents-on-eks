# ADR-0008: AWS adapter layout for versioned document ingestion

- Status: Accepted
- Date: 2026-07-12

## Context

ADR-0007 defines an application-owned ingestion pipeline with isolated candidate generations, tenant-scoped leases, fencing tokens, immutable embedding profiles and one atomic activation contract. The second Phase 3 increment must implement these ports with AWS services without introducing Bedrock managed Agents, Bedrock Knowledge Bases, OpenSearch Serverless or direct boto3 dependencies in domain/application code.

The adapters must preserve rollback safety after ambiguous network failures, prevent candidate generations from becoming retrievable, support tenant isolation and keep every AWS resource identifier configurable.

## Decision

### DynamoDB control plane

Documents, ingestion jobs, fingerprint claims, document-version leases and index-generation manifests use one configurable DynamoDB control table. Its primary key is a tenant-scoped partition key plus a typed, length-prefixed sort key. No tenant identifier is taken from a request body.

The low-level DynamoDB adapter serializes values through boto3 type serializers and exposes conditional puts, updates, deletes and transactions behind an application-independent interface.

- document writes use optimistic `revision` conditions;
- fingerprint claims create the job and canonical fingerprint marker in one transaction;
- leases use conditional expiration checks and monotonically increasing fencing tokens;
- activation uses one four-item transaction covering the document pointer, generation status, ingestion job and fingerprint marker;
- deterministic `ClientRequestToken` values make claim and activation retries idempotent;
- only genuine conditional cancellations map to `RepositoryConflictError`; throttling and infrastructure failures remain visible as AWS failures.

### S3 generation storage

Generation-scoped chunks are stored as deterministic JSON objects in a configurable private S3 bucket and prefix. Every path component is base64url encoded. A generation manifest is written before chunk objects so cleanup knows all expected keys even after a partial write.

SSE-S3 is the default object encryption. A configurable KMS key identifier enables SSE-KMS where required. Source files and upload workflows remain outside this increment.

A separate S3 manifest records the exact S3 Vectors keys for each tenant, document and generation. The manifest is persisted before each vector batch. Deleting a failed candidate therefore remains possible even when the vector service accepted a request whose response was lost.

### Bedrock embeddings

`BedrockTitanEmbeddingProvider` invokes Amazon Titan Text Embeddings V2 through Bedrock Runtime. Aliases resolve to immutable configured profiles containing revision, model ID and dimensions. Model IDs, regions, concurrency, timeouts and retry limits are configuration, not constants.

Each text is embedded independently with bounded concurrency. Responses are rejected unless dimensions, numeric values and token counts are valid. Document text is never logged.

### S3 Vectors

`S3VectorRepository` writes generation-scoped vectors and security metadata to a configurable S3 Vectors index. Batches respect the service maximum of 500 vectors.

Queries require an explicit non-empty set of authoritative active generation IDs in addition to tenant, classification and role filters. The adapter repeats these checks after receiving results and returns the generation ID with every match. Candidate generations cannot be queried accidentally by this production adapter.

## Consequences

- AWS SDK usage remains isolated under `packages/aws-clients`.
- The control table and all buckets, indexes, model IDs, regions and optional KMS keys are injected configuration.
- IAM policies can be scoped separately to the control table, document bucket, vector bucket/index and configured Bedrock models.
- Activation is atomic for metadata, while chunk/vector data is immutable and generation-scoped.
- A retriever must first resolve current active generation IDs from trusted document metadata before calling `VectorRepository.query`.
- PDF/DOCX extraction, document upload APIs, Terraform resources and real AWS integration tests remain deferred.
