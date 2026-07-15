# ADR-0011: Document infrastructure and deployment boundary

- Status: Proposed
- Date: 2026-07-15

## Context

The document API and asynchronous ingestion worker are merged into `main`, but the feature remains disabled because no deployable AWS infrastructure or Kubernetes workload definition exists in the repository.

The repository previously contained inherited workshop Terraform. That content was removed and must not be restored. New infrastructure must be purpose-built under `infra/terraform/`, must not contain hardcoded AWS account identifiers or secrets, and must preserve the application boundaries already implemented.

The API and worker are separate runtime entrypoints of the same immutable image. They do not require the same AWS permissions:

- the API registers documents, creates temporary upload URLs, promotes sources, persists ingestion jobs and sends SQS tasks;
- the worker receives tasks, owns renewable execution leases, reads source content, invokes Bedrock embeddings, writes chunks and publishes S3 Vectors generations.

The repository already uses GitHub Actions for quality gates. GitLab CI is not part of this project.

## Decision

### Scope and prerequisites

This infrastructure increment provisions the document data plane and deploys the existing API and worker to an existing EKS platform.

The following are inputs and are not created by this increment:

- AWS account and target region;
- VPC and private subnets;
- EKS cluster and node capacity;
- Cognito user pool and application clients;
- DNS, certificate, ingress controller and WAF integration;
- shared CloudWatch and OpenTelemetry platform components.

A later platform-foundation increment may create those prerequisites. This document-infrastructure increment must consume them only through typed Terraform variables, data sources or remote-state outputs.

### Repository layout

Infrastructure code will use the following boundaries:

```text
infra/
├── terraform/
│   ├── modules/
│   │   ├── document-data-plane/
│   │   ├── document-workload-identity/
│   │   └── document-observability/
│   └── environments/
│       └── dev/
└── helm/
    └── ia-agents-platform/
```

Modules must expose typed inputs and outputs, include validation, and avoid assumptions about AWS account IDs, ARNs, names or regions.

### Terraform-managed resources

The document data plane will manage:

- a DynamoDB control table for documents, jobs, generations, leases and coordination records;
- a private versioned S3 bucket for immutable sources, temporary uploads and extracted chunks;
- a mandatory lifecycle rule for the complete temporary-upload prefix;
- an encrypted FIFO SQS ingestion queue;
- an encrypted dead-letter queue and redrive policy;
- an S3 Vectors vector bucket and index;
- optional customer-managed KMS keys when required by environment policy;
- CloudWatch log groups, alarms and metric filters specific to the document API and worker;
- separate workload IAM roles and EKS Pod Identity associations for API and worker service accounts.

Native Terraform AWS provider resources will be used for S3 Vectors. The provider version will be pinned to a release that supports both the vector bucket and vector index resources, and the provider lock file will be committed.

### Workload identity

EKS Pod Identity is the selected workload-identity mechanism.

Two Kubernetes service accounts and two IAM roles are required:

```text
ia-agents-api     -> document API role
ia-agents-worker  -> document ingestion worker role
```

The API role must not receive Bedrock model invocation or S3 Vectors mutation permissions. The worker role must not receive upload-URL creation or unrelated platform-administration permissions.

Node roles are not application roles. Pods must use the default AWS SDK credential chain and must not receive static AWS credentials through Kubernetes Secrets or environment values.

### IAM permission boundaries

The API role is limited to the resources and operations needed for:

- document and ingestion-job control-plane records;
- submission lease acquisition and release;
- temporary upload signing and metadata inspection;
- conditional source promotion and temporary-object cleanup;
- SQS task submission;
- non-costly readiness checks.

The worker role is limited to the resources and operations needed for:

- SQS receive, visibility renewal and acknowledgement;
- document, job, lease and generation coordination records;
- immutable source reads and chunk writes;
- configured Bedrock embedding model invocation;
- configured S3 Vectors index publication and readiness checks.

IAM policies must scope resources to module outputs and configured prefixes. Wildcard actions or resources require an explicit documented justification and a regression test or policy check.

### Helm deployment

One chart deploys two workloads from the same immutable image digest:

- `Deployment` for the FastAPI API entrypoint;
- `Deployment` for the document worker entrypoint.

The chart must provide separate service accounts, environment values, probes, resources, pod security settings, disruption budgets and autoscaling controls. The image tag `latest` is forbidden. A deployment must reference an immutable digest or an immutable release version resolved to a digest.

Application configuration must use ConfigMaps for non-sensitive values and Secrets Manager references for secrets. Tokens, complete prompts and document content must not be placed in Kubernetes configuration or logs.

### GitHub Actions

GitHub Actions is the only CI/CD system for this repository.

The existing Python quality workflow remains the source-code quality gate. Infrastructure work will add separate workflows with minimal permissions:

1. pull-request validation without AWS credentials:
   - Terraform formatting, initialization without backend access, validation and tests;
   - policy and security checks;
   - Helm linting and rendering;
   - rendered-manifest schema validation;
2. environment plan using GitHub OIDC and a read-oriented Terraform role;
3. protected apply using GitHub OIDC, a separate apply role and a GitHub deployment environment requiring approval.

No long-lived AWS access key may be stored in GitHub secrets. Deployment workflows use `id-token: write` only in jobs that actually assume an AWS role.

Remote-state backend parameters are injected through workflow/environment configuration. Bucket names, state keys, regions and role ARNs are never committed as fixed project values.

### State and change safety

Terraform state is remote, encrypted and access-controlled. Backend bootstrap is managed outside the document module and supplied as an environment prerequisite.

Production-affecting applies must use:

- a reviewed plan generated from the exact commit being deployed;
- a protected GitHub environment;
- concurrency controls preventing overlapping applies;
- a non-force deployment path;
- explicit failure reporting.

### Observability and sensitive-data controls

The API and worker emit OpenTelemetry-compatible traces, metrics and structured logs to the shared platform pipeline and CloudWatch.

Infrastructure alarms cover at least:

- ingestion DLQ messages;
- age of the oldest ingestion message;
- worker iteration failures;
- repeated lease or visibility-heartbeat failures;
- API and worker readiness failures;
- DynamoDB throttling;
- S3 or S3 Vectors access failures.

Logs must not contain JWTs, AWS credentials, source documents, complete prompts, model responses or provider error payloads that may expose resource details.

## Alternatives considered

### Reuse the removed workshop Terraform

Rejected. It was unrelated to the platform architecture and previously contained hardcoded backend configuration.

### Use one IAM role for API and worker

Rejected. It would grant the HTTP API unnecessary Bedrock and vector-publication permissions and would prevent a meaningful least-privilege boundary.

### Use node IAM roles for application access

Rejected. Application permissions must be isolated by Kubernetes service account and auditable independently from node permissions.

### Use static AWS credentials in GitHub Actions

Rejected. GitHub OIDC provides short-lived credentials and allows trust to be restricted to repository, branch and deployment environment context.

### Automatically apply every merge to `main`

Rejected. Infrastructure apply requires a reviewed plan, environment protection and explicit approval.

### Use CloudFormation or imperative scripts for S3 Vectors

Rejected. Native Terraform AWS provider resources exist for the required S3 Vectors vector bucket and index, so the resource lifecycle can remain inside the Terraform state and module graph.

## Consequences

- The document feature remains disabled until the required resources and Helm values are deployed.
- API and worker compromise have different AWS blast radii.
- The EKS cluster must support EKS Pod Identity and run its agent before workload deployment.
- Environment prerequisites must expose stable inputs without embedding account-specific identifiers in the repository.
- GitHub Actions becomes the single auditable path for infrastructure validation, planning and apply.
- The first deployable environment is `dev`; additional environments reuse the same modules rather than copying resources.
- Infrastructure implementation and real AWS integration tests remain separate reviewed sub-lots.

## Required follow-up

1. implement and test the Terraform document data-plane module;
2. implement the workload-identity module and least-privilege policies;
3. implement the Helm chart for API and worker;
4. extend GitHub Actions with infrastructure validation, plan and protected apply workflows;
5. deploy a development environment and run real AWS integration tests;
6. update this ADR from `Proposed` to `Accepted` only after the architecture sub-lot is approved.
