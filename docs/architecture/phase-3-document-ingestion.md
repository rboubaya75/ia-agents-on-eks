# Phase 3 — Document ingestion foundation

## Implemented increment

This increment establishes a versioned, AWS-independent ingestion core:

1. load trusted document metadata by tenant and document ID;
2. resolve an immutable embedding profile;
3. derive authorization and ingestion fingerprints with canonical JSON;
4. acquire a document-version lease with a fencing token;
5. claim the fingerprinted job;
6. create an isolated candidate index generation;
7. extract and revalidate tenant, document and source-version identity;
8. create deterministic bounded chunks through `ChunkingStrategy`;
9. request embeddings in configurable batches;
10. validate model ID, dimensions, cardinality and finite vector values;
11. write candidate chunks and vectors under the generation ID;
12. mark the generation ready;
13. atomically activate the generation and complete the job;
14. delete the superseded generation on a best-effort basis;
15. delete only the candidate generation after failure.

## Publication model

```text
active generation N
        │
        ├── remains authoritative during reindex
        │
        └── candidate generation N+1
                ├── chunks
                ├── vectors
                └── ready manifest
                        ↓
              atomic metadata activation
                        ↓
              generation N+1 becomes active
```

A failed reindex leaves generation N and the document's active pointer unchanged. Candidate data is generation-scoped, so cleanup cannot delete the active generation.

## Concurrency model

Idempotency and mutual exclusion are separate:

- the fingerprint identifies one exact ingestion configuration;
- the lease protects one tenant/document/source-version scope;
- the fencing token prevents a stale worker from activating after its lease has been replaced;
- the document revision prevents activation after concurrent metadata or authorization changes.

A failed or stale running job may be replaced only by a job carrying a newer fencing token. A completed fingerprint remains canonical and is returned idempotently.

## Security properties

- tenant context is supplied from the already verified principal;
- repository reads, leases, generations and cleanup are tenant-scoped;
- extractor identity is treated as untrusted and revalidated;
- empty `allowedRoles` is invalid;
- classification and roles participate in the authorization checksum and fingerprint;
- activation fails after a concurrent document revision change;
- no source document, extracted content, token or complete prompt is logged.

## Deterministic chunk metadata

Each chunk contains the active candidate generation ID, tenant and document identifiers, source version and URI, title, section, optional page, normalized offsets, classification, allowed roles, chunk sequence, chunking version, SHA-256 checksum and deterministic chunk ID.

`maxCharacters` is always respected. Overlap is configurable, while a short final chunk is allowed instead of merging it into an oversized predecessor.

## Tests

The increment covers:

- deterministic chunking and strict maximum size;
- permission and embedding-profile changes forcing reingestion;
- stable embedding model identity and dimensions across batches;
- non-finite vector rejection;
- idempotent completed ingestion and fenced stale-job takeover;
- document-version lease exclusion across different fingerprints;
- cross-tenant ingestion denial;
- mismatched extractor identity;
- candidate cleanup after partial writes;
- preservation of the previous active generation after failed reindex;
- activation conflicts after concurrent metadata changes.

## Deferred to the next Phase 3 increment

- S3 source and chunk adapters;
- PDF, DOCX and text extractors;
- Bedrock embedding adapter and immutable profile configuration;
- S3 Vectors generation-scoped adapter;
- DynamoDB document, lease, generation, activation and job adapters;
- document upload, reindex and deletion API endpoints;
- retrieval evaluation, active-generation revalidation and citation generation.
