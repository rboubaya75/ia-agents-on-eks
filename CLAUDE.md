# Project constraints

- Python 3.12, FastAPI and Pydantic v2.
- Custom agents live under `/agents`.
- Strands or LangGraph may only be introduced behind an optional adapter.
- Amazon Bedrock AgentCore Runtime is execution infrastructure only.
- Amazon Bedrock is used through the Converse API and configurable embedding models.
- Custom RAG uses Amazon S3 and S3 Vectors.
- Application data uses DynamoDB; authentication uses Cognito User Pool.
- Frontend uses React, TypeScript and Vite, hosted on private S3 behind CloudFront and WAF.
- Backend runs on EKS; infrastructure uses Terraform and Helm.
- GitHub Actions is the temporary CI system during bootstrap. AWS authentication will use GitHub OIDC when introduced.
- Never use Bedrock managed Agents, Bedrock Knowledge Bases or OpenSearch Serverless.
- Never trust tenant identifiers from request bodies.
- Domain logic must not directly depend on boto3.
- Every feature requires tests and every architecture change requires an ADR.
- Work one phase at a time. Report lint, type checking and tests without hiding failures.
