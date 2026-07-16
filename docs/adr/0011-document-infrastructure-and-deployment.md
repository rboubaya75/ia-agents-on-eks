# ADR-0011: Document infrastructure and deployment boundary

- Status: Accepted
- Date: 2026-07-15

## Context

The document API and asynchronous ingestion worker are merged into `main`, but the feature remains disabled because no deployable AWS infrastructure or Kubernetes workload definition exists in the repository.

The repository previously contained inherited workshop Terraform. That content was removed and must not be restored. New infrastructure must be purpose-built under `infra/terraform/`, must not contain hardcoded AWS identifiers or secrets, and must preserve the application boundaries already implemented.

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
- EKS Pod Identity Agent and node-level controls required to isolate application pods from node credentials;
- Cognito user pool and application clients;
- DNS, certificate, ingress controller and WAF integration;
- shared CloudWatch and OpenTelemetry platform components;
- remote Terraform state infrastructure.

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
- an encrypted FIFO dead-letter queue and redrive policy;
- an S3 Vectors vector bucket and one or more versioned indexes;
- optional customer-managed KMS keys when required by environment policy;
- CloudWatch log groups, alarms and metric filters specific to the document API and worker;
- separate workload IAM roles and EKS Pod Identity associations for API and worker service accounts.

Native Terraform AWS provider resources will be used for S3 Vectors. The provider version will be pinned to a release that supports both the vector bucket and vector index resources, and the provider lock file will be committed.

### Workload identity and node-credential isolation

EKS Pod Identity is the selected workload-identity mechanism.

Two Kubernetes service accounts and two IAM roles are required:

```text
ia-agents-api     -> document API role
ia-agents-worker  -> document ingestion worker role
```

The API role must not receive Bedrock model invocation or S3 Vectors mutation permissions. The worker role must not receive upload-URL creation or unrelated platform-administration permissions.

Node roles are not application roles. The following controls are mandatory:

- API and worker pods use `hostNetwork: false`;
- application pods cannot reach EC2 Instance Metadata Service and cannot obtain the node IAM role;
- network and node controls preserve the Pod Identity Agent credential endpoint while denying IMDS;
- pods use the default AWS SDK credential chain;
- static AWS credentials are forbidden in Kubernetes Secrets, ConfigMaps and environment values;
- rendered-manifest tests reject `hostNetwork: true` and static AWS credential variables;
- deployment validation confirms each pod resolves the expected role with `sts:GetCallerIdentity`.

The exact IMDS control may be enforced by the EKS platform, CNI, node firewall or equivalent cluster mechanism, but it is a verifiable prerequisite before the document workloads are enabled.

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

IAM policies must scope resources to module outputs and configured prefixes. Wildcard actions or resources require an explicit documented justification and a regression test or policy check. Negative-permission integration tests must prove that the API cannot invoke Bedrock or mutate S3 Vectors and that the worker cannot create upload sessions or send ingestion tasks.

### S3 data controls

The document bucket must enforce:

- complete public-access blocking;
- bucket-owner-enforced object ownership;
- versioning;
- default encryption and optional customer-managed KMS integration;
- denial of non-TLS requests;
- a lifecycle rule scoped only to the complete temporary-upload prefix;
- cleanup of abandoned multipart uploads;
- no automatic expiration of immutable sources or active chunk generations;
- prefix-scoped IAM and KMS permissions for API and worker operations.

Temporary-upload retention, source retention and inactive-generation cleanup are distinct policies. A lifecycle change must not be able to expire immutable sources or the active indexed generation.

### SQS reliability controls

The ingestion queue and dead-letter queue must both be FIFO and encrypted. The module must configure:

- long polling;
- visibility timeout and application heartbeat values with a validated safety relationship;
- configurable retention and redrive count;
- a redrive allow policy that accepts only the intended source queue;
- alarms for dead-letter messages and age of the oldest message;
- tests proving Terraform and application visibility-timeout settings remain consistent.

The worker heartbeat interval must remain safely below the queue visibility timeout. A configuration that can make an active ingestion visible before lease/heartbeat renewal must fail validation.

### DynamoDB durability controls

The control table must use encryption, point-in-time recovery and deletion protection by default. The first environment uses on-demand capacity unless an alternative is justified by measured demand.

TTL is permitted only for explicitly ephemeral records. It must not expire document metadata, retained audit records, active jobs or active generations. Terraform tests must cover the key schema and indexes required by the application adapters.

### S3 Vectors index immutability

An index identity is immutable and tied to the resolved embedding contract:

```text
embedding profile alias
embedding profile revision
vector dimension
distance metric
index generation
```

Changing vector dimension, distance metric, data type, encryption contract or embedding profile revision creates a new index. It must never replace the active index in place.

Terraform must apply `prevent_destroy` to active indexes by default. Destructive override is an exceptional, separately approved operation and is disabled by default.

Migration uses a create-before-cutover sequence:

1. create a new versioned index;
2. retain the current index;
3. re-index all required documents;
4. verify coverage and embedding compatibility;
5. deploy application configuration selecting the new index;
6. observe a defined stabilization period;
7. retire the previous index in a separate reviewed operation.

### Helm deployment

One chart deploys two workloads from the same immutable image digest:

- `Deployment` for the FastAPI API entrypoint;
- `Deployment` for the document worker entrypoint.

The chart must provide separate service accounts, environment values, probes, resources, pod security settings, disruption budgets and autoscaling controls. The image tag `latest` is forbidden. A deployment must reference an immutable digest or an immutable release version resolved to a digest.

API probes use the HTTP health and composite-readiness endpoints. The worker must not expose a fictitious HTTP readiness probe: its startup and liveness checks use a dedicated worker-health mechanism, and graceful termination stops polling, visibility heartbeats and lease renewal before process exit.

Application configuration must use ConfigMaps for non-sensitive values and Secrets Manager references for secrets. Tokens, complete prompts and document content must not be placed in Kubernetes configuration or logs.

### GitHub Actions and AWS OIDC

GitHub Actions is the only CI/CD system for this repository.

The existing Python quality workflow remains the source-code quality gate. Infrastructure work will add separate workflows with minimal permissions:

1. pull-request validation without AWS credentials:
   - Terraform formatting, initialization without backend access, validation and tests;
   - policy and security checks;
   - Helm linting and rendering;
   - rendered-manifest schema validation;
2. environment plan using GitHub OIDC and a read-oriented Terraform role;
3. protected apply using GitHub OIDC, a separate apply role and a GitHub deployment environment requiring approval.

No long-lived AWS access key may be stored in GitHub secrets. Deployment workflows use `id-token: write` only in jobs that actually assume an AWS role; the default workflow permission remains `contents: read`.

OIDC trust policies must use exact `StringEquals` conditions for both `token.actions.githubusercontent.com:aud` and `token.actions.githubusercontent.com:sub`. The subject must bind the exact repository to the selected GitHub deployment environment. Repository-wide or organization-wide wildcards are forbidden.

Plan and apply use separate GitHub environments and separate IAM roles. Apply environments accept deployments from `main` only and require approval. The plan role may refresh target state and write only the remote-state lock required by the selected backend; it must not mutate target infrastructure resources.

Remote-state backend parameters are injected through workflow/environment configuration. Bucket names, state keys, regions and role ARNs are never committed as fixed project values.

### State, plan artifacts and change safety

Terraform state is remote, encrypted and access-controlled. Backend bootstrap is managed outside the document module and supplied as an environment prerequisite.

A saved Terraform plan is treated as a sensitive deployment artifact. The plan workflow must produce:

```text
tfplan
tfplan.sha256
deployment-manifest.json
```

The deployment manifest binds at least:

- environment;
- exact commit SHA;
- Terraform version;
- provider-lock-file digest;
- plan digest;
- creation timestamp and expiration.

Plan artifacts use the shortest practical retention, restricted access and no public or unredacted PR publication. The human-readable PR summary must redact sensitive values.

Apply must verify the same environment, commit, Terraform version, provider lock digest and plan digest before using the saved plan. It must also enforce concurrency controls preventing overlapping applies to the same environment, use a non-force deployment path and report partial failures explicitly.

### Configuration traceability

Every runtime configuration value required by the document feature must have one authoritative source:

- Terraform outputs for created AWS resources and workload roles;
- protected GitHub environment configuration for external prerequisites and deployment-only values;
- Helm values generated from approved outputs and image digests;
- no manually duplicated AWS identifier across Terraform, Helm and workflow files.

A traceability matrix in the implementation plan defines the source and consumer for every required application setting.

### Observability and sensitive-data controls

The API and worker emit OpenTelemetry-compatible traces, metrics and structured logs to the shared platform pipeline and CloudWatch.

Infrastructure alarms cover at least:

- ingestion dead-letter messages;
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

### Mutate an active S3 Vectors index in place

Rejected. Embedding-contract changes require a new versioned index, re-indexing and an explicit cutover. In-place replacement risks irreversible data loss and retrieval incompatibility.

### Use CloudFormation or imperative scripts for S3 Vectors

Rejected. Native Terraform AWS provider resources exist for the required S3 Vectors vector bucket and vector index, so the resource lifecycle can remain inside the Terraform state and module graph.

## Consequences

- The document feature remains disabled until the required resources and Helm values are deployed.
- API and worker compromise have different AWS blast radii.
- The EKS platform must support Pod Identity, isolate pods from IMDS and run the Pod Identity Agent before workload deployment.
- Active vector indexes cannot be silently replaced; embedding changes require an explicit migration.
- Environment prerequisites must expose stable inputs without embedding account-specific identifiers in the repository.
- GitHub Actions becomes the single auditable path for infrastructure validation, planning and apply.
- Saved Terraform plans become protected, short-lived deployment artifacts.
- The first deployable environment is `dev`; additional environments reuse the same modules rather than copying resources.
- Infrastructure implementation and real AWS integration tests remain separate reviewed sub-lots.

## Required follow-up

1. implement and test the Terraform document data-plane module, including immutable vector-index generations;
2. implement workload identity, IMDS isolation checks and least-privilege policies;
3. implement the Helm chart with separate API and worker health contracts;
4. extend GitHub Actions with infrastructure validation, exact OIDC trust, plan integrity and protected apply workflows;
5. deploy a development environment and run positive and negative AWS integration tests;
6. retain this ADR as the accepted boundary and record implementation-specific constraints in the corresponding phase documentation.
