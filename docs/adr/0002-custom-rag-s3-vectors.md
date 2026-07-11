# ADR-0002: Application-owned RAG with Amazon S3 Vectors

- Status: Accepted
- Date: 2026-07-11

## Decision

The application owns document parsing, chunking, metadata, embeddings, retrieval, filtering, prompt construction, grounding and citations. Chunks are stored in Amazon S3 and vectors in Amazon S3 Vectors.

## Consequences

- Bedrock Knowledge Bases and OpenSearch Serverless are prohibited.
- `VectorQuery` requires tenant, classification and role filters.
- Physical vector index names are not exposed to API clients or domain code.
