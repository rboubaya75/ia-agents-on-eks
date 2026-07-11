# IA Agents on EKS

Production-grade AWS agentic AI platform built with custom Python agents, FastAPI, Amazon Bedrock AgentCore Runtime, Amazon Bedrock Converse API, S3 Vectors, DynamoDB, Cognito, EKS, Terraform and Helm.

## Current status

Phase 1 foundation is in progress. This phase contains only domain models, typed ports, agent contracts, security primitives, observability context and local fakes. It contains no real AWS integration.

## Non-negotiable architecture rules

- Agent logic is custom code under `agents/`.
- AgentCore Runtime is execution infrastructure only.
- No Bedrock managed Agents.
- No Bedrock Knowledge Bases.
- No OpenSearch Serverless.
- RAG is implemented in application code with S3 Vectors.
- Domain code never imports boto3.
- Tenant identity is derived from verified Cognito claims, never from request bodies.

## Local quality checks

```bash
uv sync
make check
```

Python 3.12 is required.

## Repository note

The repository currently contains inherited AWS workshop files at the root and under `home/` and `modules/`. They are outside the Phase 1 application scope and are documented as a cleanup risk in `docs/architecture/phase-1-baseline-review.md`. They must not be treated as deployable platform infrastructure.
