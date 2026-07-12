# Phase 3 — Document ingestion foundation

## Implemented increment

This increment establishes the AWS-independent document ingestion core:

1. load trusted document metadata by tenant and document ID;
2. derive a deterministic pipeline fingerprint;
3. atomically claim the ingestion job;
4. extract sectioned text through a port;
5. revalidate tenant, document and version identity;
6. create deterministic paragraph-aware chunks;
7. request embeddings in configurable batches;
8. validate vector count and dimensions;
9. replace chunks and vectors for the source version;
10. update document and job status;
11. clean partial writes after failure.

## Domain lifecycle

Documents use these states:

- `pending_upload`;
- `uploaded`;
- `processing`;
- `indexed`;
- `failed`;
- `deleting`;
- `deleted`.

The current ingestion service accepts `uploaded`, `failed` and `indexed`. A successfully completed fingerprint is idempotent and returns the canonical prior job. An identical running fingerprint is rejected as already in progress.

## Deterministic chunk metadata

Each chunk contains:

- trusted tenant and document identifiers;
- source version and URI;
- title, section, optional page and normalized offsets;
- language, classification and allowed roles;
- chunk sequence and chunking version;
- SHA-256 content checksum;
- a deterministic SHA-256 chunk ID.

Vector records additionally retain the embedding model ID, dimensions and pipeline version.

## Security properties

- Tenant context is supplied by the caller from the already verified principal; it is never accepted from document content.
- Repository reads are tenant-scoped.
- Extracted identity is revalidated before embedding.
- Empty `allowedRoles` is invalid and therefore deny-by-default.
- Cleanup is tenant, document and version scoped.
- No prompt, token, source document or extracted content is logged by this increment.

## Tests

The increment covers:

- deterministic chunking and metadata propagation;
- invalid role and offset validation;
- embedding batching and response validation;
- idempotent completed ingestion;
- atomic fingerprint claim semantics;
- cross-tenant ingestion denial;
- mismatched extractor identity;
- retry after a failed fingerprint;
- cleanup after a partial vector write.

## Deferred to the next Phase 3 increment

- S3 source and chunk adapters;
- PDF/DOCX/text extractors;
- Bedrock embedding adapter;
- S3 Vectors adapter;
- DynamoDB document and ingestion-job adapters;
- document upload and ingestion API endpoints;
- signed pagination, deletion and reindex workflows;
- retrieval evaluation and citation generation.
