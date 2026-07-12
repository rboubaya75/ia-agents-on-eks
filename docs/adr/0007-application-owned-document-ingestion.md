# ADR-0007: Application-owned versioned document ingestion

- Status: Accepted
- Date: 2026-07-12

## Context

The platform requires multi-tenant retrieval-augmented generation over private documents. The project rules exclude Bedrock Knowledge Bases and require RAG to remain in application code, with Bedrock as the future embedding/model provider and S3 Vectors as the future vector store.

The ingestion path must be testable before AWS resources exist. It must also be deterministic, idempotent, tenant-aware, safe to retry after partial failures, and incapable of making a partial reindex visible to retrieval.

## Decision

The application layer owns ingestion orchestration and depends only on ports:

- `DocumentRepository` for document metadata with optimistic revisions;
- `IngestionJobRepository` for idempotency history and fenced claims;
- `DocumentIngestionLeaseRepository` for one active worker per tenant, document and source version;
- `TextExtractor` for source-specific extraction;
- `ChunkingStrategy` for deterministic chunk creation;
- `EmbeddingProvider` for immutable embedding-profile resolution and embedding batches;
- `ChunkStore` and `VectorRepository` for generation-scoped candidate data;
- `IndexGenerationRepository` for generation manifests;
- `IndexActivationRepository` for the atomic metadata commit that activates one ready generation and completes its job.

Each ingestion writes a new isolated generation. Candidate chunks and vectors are never written over the active generation. After all candidate data and manifest counts are valid, one atomic activation changes the document's `activeGenerationId`, marks the generation active and marks the job succeeded. Only after this commit may the previous generation be deleted. Failed candidates are deleted without touching the active generation.

A document-version lease is independent from the idempotency fingerprint. Its scope is `tenantId + documentId + sourceVersion`, so pipelines with different model or pipeline fingerprints cannot modify the same document version concurrently. Leases carry monotonically increasing fencing tokens. Activation rejects a token that is not newer than the document's last accepted token.

The ingestion fingerprint uses canonical JSON and includes source identity, document metadata that affects retrieval, the authorization checksum, chunking version, pipeline version and an immutable resolved embedding profile. The authorization checksum includes classification and sorted allowed roles. A permission change therefore invalidates prior idempotency even when source bytes are unchanged.

Embedding responses must match the resolved model ID and dimensions for every batch. Vector counts, dimensions and finite numeric values are validated before candidate publication.

The initial chunker is paragraph-aware and character-bounded. `maxCharacters` is a hard invariant; `minimumCharacters` is a preference and may not be satisfied for a final tail. `ChunkingStrategy` is a true application port and exposes an immutable version.

## Consequences

The retriever must use the document's active-generation pointer as the authority and must revalidate document authorization before returning chunk content. Candidate or superseded generations must never be treated as authoritative solely because they exist in a vector store.

A future DynamoDB implementation of `IndexActivationRepository` must use a transaction or equivalent conditional commit across the document pointer, generation manifest and ingestion job. A future lease adapter must use conditional writes, expiration and fencing tokens.

AWS-specific extractors, S3 storage, Bedrock embeddings, S3 Vectors and DynamoDB persistence remain separate adapters for a later increment. No domain or application service depends directly on boto3.
