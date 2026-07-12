# ADR-0007: Application-owned deterministic document ingestion

- Status: Accepted
- Date: 2026-07-12

## Context

The platform requires multi-tenant retrieval-augmented generation over private documents. The project rules exclude Bedrock Knowledge Bases and require RAG to remain in application code, with Bedrock as the future embedding/model provider and S3 Vectors as the future vector store.

The ingestion path must be testable before AWS resources exist. It must also be deterministic, idempotent, tenant-aware and safe to retry after partial failures.

## Decision

The application layer owns the ingestion orchestration and depends only on ports:

- `DocumentRepository` for document metadata and lifecycle state;
- `IngestionJobRepository` for job history and an atomic fingerprint claim;
- `TextExtractor` for source-specific text extraction;
- `ChunkingStrategy` for deterministic chunk creation;
- `EmbeddingProvider` for embedding batches;
- `ChunkStore` for chunk content;
- `VectorRepository` for vector records.

The initial chunking strategy is paragraph-aware and character-bounded. It normalizes whitespace, preserves section and page metadata, uses configurable overlap, and generates deterministic chunk IDs and SHA-256 checksums. Offsets refer to the normalized section text.

An ingestion fingerprint is derived from the trusted tenant, document ID, source version, source checksum, chunking version, embedding model alias and pipeline version. The repository `claim` operation is the concurrency boundary: a future DynamoDB adapter must implement it atomically with a conditional write.

Embedding responses are validated for batch cardinality and stable dimensions. Chunks and vectors are replaced only after extraction, chunking and embedding have succeeded. Replacement and cleanup are scoped to one tenant, document and source version. Partial writes trigger best-effort cleanup and a failed job record.

The extractor output is treated as untrusted. Tenant, document and source-version identity are revalidated before content can reach embeddings or storage. Documents and chunks require at least one allowed role; an empty role set is denied by validation.

## Consequences

The pipeline is fully testable with in-memory adapters and does not require boto3 or an AWS account. AWS-specific extractors, S3 chunk storage, Bedrock embeddings, S3 Vectors and DynamoDB persistence remain separate adapters for a later increment.

The character-based chunker is a deterministic baseline, not the final retrieval-quality policy. Token-aware and structure-aware strategies can be introduced behind `ChunkingStrategy` with a new version and without changing the orchestrator.

A production repository must preserve atomic fingerprint claims, version-scoped deletion and tenant isolation. Failure to implement those semantics is a contract violation.
