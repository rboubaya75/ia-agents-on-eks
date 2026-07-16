# IA Agents on EKS

Production-grade AWS agentic AI platform built with custom Python agents, FastAPI, Amazon Bedrock AgentCore Runtime, Amazon Bedrock Converse API, S3 Vectors, DynamoDB, Cognito, EKS, Terraform and Helm.

## Current status

Phase 3 delivered the feature-gated document API and asynchronous ingestion worker. Phase 4A accepted the infrastructure and deployment boundary. Phase 4B now implements the Terraform document data plane: DynamoDB, private S3 storage, encrypted FIFO SQS with FIFO DLQ, immutable S3 Vectors index generations and optional KMS encryption.

No AWS environment is deployed by the repository yet. Workload IAM, EKS Pod Identity and Helm remain Phase 4C; GitHub OIDC plan/apply workflows remain Phase 4D; real AWS deployment and integration validation remain Phase 4E.

## Non-negotiable architecture rules

- Agent logic is custom code under `agents/`.
- AgentCore Runtime is execution infrastructure only.
- No Bedrock managed Agents.
- No Bedrock Knowledge Bases.
- No OpenSearch Serverless.
- RAG is implemented in application code with S3 Vectors.
- Domain code never imports boto3.
- Tenant identity is derived from verified Cognito claims, never from request bodies.
- GitHub Actions is the sole CI/CD system.
- Terraform lives under `infra/terraform/`; Helm lives under `infra/helm/`.
- AWS identifiers, credentials and secrets are never hardcoded.

## Quality checks

Python 3.12 is required.

```bash
uv sync
make check
```

Terraform 1.15.8 is required for the Phase 4B module.

```bash
terraform fmt -check -recursive infra/terraform
terraform -chdir=infra/terraform/modules/document-data-plane init -backend=false
terraform -chdir=infra/terraform/modules/document-data-plane validate
terraform -chdir=infra/terraform/modules/document-data-plane test
terraform -chdir=infra/terraform/environments/dev init -backend=false -lockfile=readonly
terraform -chdir=infra/terraform/environments/dev validate
```

## Infrastructure boundary

The removed AWS workshop Terraform must not be restored. Deployable platform infrastructure is implemented only under `infra/terraform/` according to ADR-0011. Backend configuration and environment-specific AWS values are injected through uncommitted local files or protected GitHub environment configuration.
