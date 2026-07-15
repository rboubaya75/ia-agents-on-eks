# Phase 4 — Document infrastructure plan

## Objective

Deliver the AWS and Kubernetes infrastructure required to enable the document API and asynchronous ingestion worker already merged into `main`, without introducing frontend work, durable deletion or agent features.

This phase uses Terraform, Helm and GitHub Actions only.

## Baseline

The application currently provides:

- Cognito JWT validation and trusted tenant/user context;
- document registration and source-upload contracts;
- temporary S3 upload and conditional source promotion adapters;
- DynamoDB document, job, generation and lease adapters;
- FIFO SQS ingestion dispatch and reliable worker execution;
- Bedrock embedding integration;
- S3 chunk storage and S3 Vectors publication;
- feature-gated readiness checks;
- Python quality gates on GitHub Actions.

The repository contains no deployable Terraform or Helm implementation. The document feature therefore remains disabled by default.

## Scope boundary

### Included

- document-specific DynamoDB, S3, SQS, S3 Vectors and optional KMS resources;
- separate API and worker workload identities;
- document-specific CloudWatch resources;
- Helm chart definitions for API and worker;
- GitHub Actions infrastructure validation, plan and protected apply workflows;
- development-environment composition;
- real AWS integration tests after deployment.

### External prerequisites

The phase consumes but does not create:

- an AWS account and region;
- remote Terraform state infrastructure;
- networking and private subnets;
- an EKS cluster with node capacity and EKS Pod Identity support;
- the EKS Pod Identity Agent;
- node or cluster controls that deny application pods access to IMDS while preserving the Pod Identity credential endpoint;
- Cognito resources;
- ingress, DNS, certificates and WAF;
- shared OpenTelemetry and CloudWatch collection components.

### Excluded

- VPC, Transit Gateway, firewall or landing-zone account vending;
- EKS cluster creation;
- Cognito provisioning;
- frontend deployment;
- durable document deletion;
- PDF or DOCX processing;
- retrieval and custom agents;
- Bedrock managed Agents, Bedrock Knowledge Bases and OpenSearch Serverless.

## Target deployment

```text
GitHub Actions
  ├── quality
  ├── infrastructure validation
  ├── environment plan via AWS OIDC
  └── protected apply via AWS OIDC
             │
             ▼
Existing AWS account and EKS cluster
  │
  ├── DynamoDB document control table
  ├── private versioned S3 document bucket
  │     ├── uploads/ temporary prefix + lifecycle
  │     ├── sources/ immutable source prefix
  │     └── chunks/ generation prefix
  ├── encrypted FIFO SQS ingestion queue
  │     └── encrypted FIFO DLQ
  ├── S3 Vectors vector bucket
  │     ├── active versioned index
  │     └── migration index generations
  ├── optional KMS keys
  ├── CloudWatch logs, metrics and alarms
  └── EKS
        ├── API Deployment
        │     └── ia-agents-api service account / API IAM role
        └── Worker Deployment
              └── ia-agents-worker service account / worker IAM role
```

The API and worker use the same immutable container image but different commands, service accounts, scaling rules, health contracts and IAM permissions.

## Terraform layout

```text
infra/terraform/
├── modules/
│   ├── document-data-plane/
│   │   ├── dynamodb.tf
│   │   ├── s3.tf
│   │   ├── sqs.tf
│   │   ├── s3vectors.tf
│   │   ├── kms.tf
│   │   ├── variables.tf
│   │   ├── outputs.tf
│   │   └── tests/
│   ├── document-workload-identity/
│   │   ├── api-role.tf
│   │   ├── worker-role.tf
│   │   ├── pod-identity.tf
│   │   ├── variables.tf
│   │   ├── outputs.tf
│   │   └── tests/
│   └── document-observability/
│       ├── logs.tf
│       ├── alarms.tf
│       ├── variables.tf
│       ├── outputs.tf
│       └── tests/
└── environments/
    └── dev/
        ├── backend.hcl.example
        ├── main.tf
        ├── providers.tf
        ├── variables.tf
        ├── outputs.tf
        └── terraform.tfvars.example
```

Environment examples contain placeholders only. Real backend values, role ARNs, account IDs, names and secrets are supplied through protected GitHub environment configuration or local uncommitted files.

## Module contracts

### `document-data-plane`

Inputs include:

- resource-name prefix;
- AWS region;
- environment name;
- common tags;
- source and chunk prefixes;
- temporary-upload prefix and lifecycle duration;
- queue visibility timeout, heartbeat interval, retention and redrive count;
- embedding profile alias and immutable revision;
- vector dimension, distance metric and index generation;
- encryption mode and optional KMS key inputs;
- deletion protection and retention controls.

Outputs include:

- DynamoDB table name and ARN;
- document bucket name and ARN;
- lifecycle rule identifier;
- ingestion queue URL and ARN;
- DLQ URL and ARN;
- S3 Vectors bucket and versioned index names and ARNs;
- selected index generation and embedding-profile revision;
- KMS key ARNs when managed by the module.

### `document-workload-identity`

Inputs include:

- EKS cluster name;
- Kubernetes namespace;
- API and worker service-account names;
- all resource ARNs and configured prefixes from the data-plane module;
- permitted Bedrock embedding model or inference-profile ARNs;
- common tags.

Outputs include:

- API role ARN;
- worker role ARN;
- Pod Identity association identifiers;
- service-account names consumed by Helm.

### `document-observability`

Inputs include:

- API and worker workload names;
- log-retention policy;
- ingestion queue and DLQ identifiers;
- DynamoDB table identifier;
- notification target inputs;
- alarm thresholds.

Outputs include log-group and alarm identifiers.

## Data-plane invariants

### S3 bucket

The module must enforce:

- complete public-access blocking;
- bucket-owner-enforced object ownership;
- versioning;
- default encryption;
- denial of non-TLS requests;
- temporary lifecycle scoped to the complete upload prefix only;
- cleanup of abandoned multipart uploads;
- no expiration of immutable source objects;
- no expiration of active chunk generations;
- separate retention controls for temporary uploads and inactive generations.

When a customer-managed KMS key is selected, key permissions are separated by workload and limited to the expected S3 encryption context. API permissions cover temporary upload and source promotion. Worker permissions cover source decryption and chunk encryption.

Terraform tests must prove that a lifecycle-policy change cannot select the source prefix or active chunk generation.

### FIFO SQS and DLQ

Both queues are FIFO and encrypted. Required settings include:

- long polling;
- configurable message retention;
- visibility timeout validated against the application heartbeat interval;
- configurable redrive count;
- DLQ redrive allow policy limited to the ingestion queue;
- dead-letter and oldest-message alarms.

The heartbeat interval must remain safely below the queue visibility timeout. Terraform validation must fail unsafe combinations and an integration test must verify that long-running work does not become visible while its ownership and heartbeat remain valid.

### DynamoDB control table

The table must use:

- encryption;
- point-in-time recovery;
- deletion protection by default;
- on-demand capacity for the first environment unless measured demand justifies another mode;
- TTL only for explicitly ephemeral coordination records;
- no TTL on document metadata, retained jobs, audit records or active generations.

Terraform tests must cover the key schema and every index required by the application adapters.

### S3 Vectors index generations

An index is identified by an immutable embedding contract:

```text
embedding profile alias
embedding profile revision
vector dimension
distance metric
index generation
```

The generated index name must include or deterministically represent that contract. `dimension`, `distance_metric`, data type, encryption contract and profile revision are not mutable settings of an active index.

Active indexes use `prevent_destroy = true` by default. A destructive override is not exposed as a routine environment value; it requires a separately reviewed retirement operation.

A profile or vector-contract change follows this migration:

1. create a new versioned index;
2. keep the current index serving existing traffic;
3. re-index all required documents;
4. verify source-to-index coverage and embedding compatibility;
5. deploy the application configuration selecting the new index;
6. observe a stabilization period;
7. retire the old index in a separate approved change.

Terraform tests must prove that a dimension, metric or profile revision change creates a distinct index and never destroys the active index.

## IAM separation

### API role

The API role is restricted to:

- document, job and submission-lease records in the configured DynamoDB table;
- temporary upload creation and metadata inspection;
- conditional copy to the immutable source prefix;
- temporary-object deletion;
- task submission to the ingestion queue;
- readiness reads for configured resources.

It must not be able to invoke Bedrock embeddings, receive SQS messages or publish vectors.

### Worker role

The worker role is restricted to:

- receiving, extending and acknowledging ingestion messages;
- job, document, lease and generation records;
- immutable source reads;
- chunk writes and inactive-generation cleanup within the configured prefix;
- configured Bedrock embedding invocation;
- configured S3 Vectors index writes and readiness reads.

It must not be able to create upload sessions, promote temporary sources, send ingestion tasks or administer unrelated platform resources.

### Pod Identity and node-role isolation

The Helm and platform contracts must enforce:

- separate API and worker service accounts;
- `hostNetwork: false` on both deployments;
- no static AWS credentials in manifests or environment values;
- no access from application pods to IMDS or the node IAM role;
- preserved access to the local EKS Pod Identity credential endpoint;
- default AWS SDK credential-chain usage.

Required validation includes:

- rendered-manifest checks rejecting `hostNetwork: true`;
- policy checks rejecting static AWS credential variable names;
- `sts:GetCallerIdentity` checks proving each workload receives its own role;
- negative AWS tests proving API and worker forbidden operations fail.

The cluster-level IMDS restriction is an external prerequisite and must be verified before enabling `IA_DOCUMENT_API_ENABLED`.

## Helm layout

```text
infra/helm/ia-agents-platform/
├── Chart.yaml
├── values.yaml
├── values.schema.json
├── templates/
│   ├── api-deployment.yaml
│   ├── api-service.yaml
│   ├── api-service-account.yaml
│   ├── worker-deployment.yaml
│   ├── worker-service-account.yaml
│   ├── configmap.yaml
│   ├── pod-disruption-budgets.yaml
│   ├── horizontal-pod-autoscalers.yaml
│   └── network-policies.yaml
└── tests/
```

Required chart controls:

- immutable image digest or immutable release version;
- separate commands for API and worker;
- separate service accounts with no automatic Kubernetes token mounting unless required;
- `hostNetwork: false`;
- CPU and memory requests and limits;
- non-root execution, read-only root filesystem and dropped Linux capabilities;
- configurable replica counts and autoscaling;
- disruption budgets;
- topology spread and anti-affinity options;
- namespace-scoped network policies that deny IMDS and preserve required Pod Identity connectivity;
- ConfigMap values containing no secrets;
- secret references resolved through the platform secret-injection mechanism.

### API health contract

The API uses:

- HTTP startup probe;
- HTTP liveness probe;
- HTTP readiness probe backed by the existing composite readiness checks.

### Worker health and termination contract

The worker is a long-polling process and must not receive a fictitious API readiness probe. It uses:

- a startup check confirming worker initialization;
- a dedicated liveness signal based on worker progress or heartbeat, not only process existence;
- graceful termination that stops receiving new messages;
- cessation of SQS visibility heartbeat and DynamoDB lease renewal before exit;
- sufficient `terminationGracePeriodSeconds` for the active iteration to stop safely;
- a `preStop` mechanism or equivalent shutdown signal.

## GitHub Actions design

### Existing workflow

`.github/workflows/quality.yml` remains the Python lint, typecheck and test gate.

### Infrastructure validation workflow

A new workflow runs on pull requests changing `infra/**`, infrastructure ADRs or infrastructure workflows. It requires no AWS credentials and performs:

- `terraform fmt -check`;
- provider initialization without remote backend access;
- `terraform validate` for modules and environment composition;
- `terraform test`;
- static security and policy checks;
- IAM policy assertions and forbidden-action tests;
- `helm lint`;
- deterministic `helm template` rendering;
- Kubernetes manifest schema validation;
- rendered-manifest security assertions;
- workflow syntax validation.

Terraform, Helm and validation-tool versions are pinned. Third-party actions are pinned to immutable commit SHAs rather than floating tags.

### OIDC trust contract

Plan and apply use separate GitHub deployment environments and separate AWS IAM roles. Trust policies must require exact equality for:

```text
token.actions.githubusercontent.com:aud = sts.amazonaws.com
token.actions.githubusercontent.com:sub = repo:rboubaya75/ia-agents-on-eks:environment:<exact-environment>
```

No repository, owner, branch or environment wildcard is permitted. The apply environment accepts deployments from `main` only and requires approval. `id-token: write` is granted only to the AWS-assuming job; other jobs retain `contents: read` only.

### Plan workflow

The plan workflow:

- is manually dispatched or called through a protected reusable workflow;
- selects the exact plan environment;
- assumes the read-oriented planning role through GitHub OIDC;
- initializes the configured remote backend;
- refreshes state with read permissions;
- may write only the backend lock required by the configured state mechanism;
- has no target-resource mutation permissions;
- creates a plan from the exact commit SHA;
- produces `tfplan`, `tfplan.sha256` and `deployment-manifest.json`;
- uploads only a redacted human-readable summary to the PR;
- never applies changes.

The deployment manifest binds:

```text
environment
commit SHA
Terraform version
provider lock-file SHA-256
plan SHA-256
creation timestamp
expiration timestamp
```

The binary plan is a sensitive artifact. Access is restricted and retention is limited to the shortest practical duration.

### Apply workflow

The apply workflow:

- uses the exact protected apply environment;
- accepts `main` only;
- requires approval;
- assumes a separate apply role through GitHub OIDC;
- verifies the environment, commit, Terraform version, provider-lock digest, plan digest and expiration;
- prevents concurrent applies to the same environment;
- applies only the reviewed saved plan;
- reports failures and possible partial changes explicitly.

No workflow stores long-lived AWS credentials or publishes an unredacted plan.

## Configuration traceability matrix

| Runtime configuration | Authoritative source | Consumer |
|---|---|---|
| Document DynamoDB table | Terraform data-plane output | API and worker Helm values |
| Document S3 bucket | Terraform data-plane output | API and worker Helm values |
| Temporary lifecycle rule ID | Terraform data-plane output | API readiness configuration |
| Ingestion queue URL | Terraform data-plane output | API and worker Helm values |
| SQS visibility timeout | Validated Terraform environment input | Worker Helm value |
| S3 Vectors bucket/index | Terraform versioned-index outputs | Worker and API readiness values |
| Embedding profile alias/revision | Protected environment configuration | Terraform index contract and worker configuration |
| Bedrock model/inference profile ARN | Protected environment configuration | Worker IAM and worker configuration |
| API role | Workload-identity output | EKS Pod Identity association |
| Worker role | Workload-identity output | EKS Pod Identity association |
| Cognito identifiers | External prerequisite input | API configuration |
| Immutable image digest | Release workflow output | Helm deployment value |

Generated deployment values may consume these outputs, but AWS identifiers must not be copied manually into committed Helm defaults or workflow source.

## Delivery increments

### Phase 4A — Architecture boundary

Deliverables:

- ADR-0011;
- this architecture and implementation plan;
- draft pull request;
- existing GitHub Actions quality workflow green.

No Terraform, Helm or AWS deployment is introduced.

### Phase 4B — Terraform document data plane

Deliverables:

- `document-data-plane` module;
- S3, FIFO SQS/DLQ, DynamoDB and versioned S3 Vectors contracts;
- development composition;
- module tests and static validation;
- infrastructure validation workflow foundation;
- updated ADR consequences where implementation constraints differ.

### Phase 4C — Workload identity and Helm

Deliverables:

- API and worker IAM roles;
- EKS Pod Identity associations;
- IMDS-isolation prerequisites and validation;
- Helm chart with API and worker deployments;
- separate API and worker health contracts;
- manifest and chart tests;
- immutable image-value contract.

### Phase 4D — GitHub Actions plan and apply

Deliverables:

- exact OIDC trust policies for plan and apply environments;
- protected plan and apply workflows;
- separate plan and apply role contracts;
- remote-state input contract;
- deployment concurrency, expiry and artifact-integrity controls.

### Phase 4E — Development deployment and integration validation

Deliverables:

- deployed `dev` resources;
- workload identity and node-role isolation verification;
- API and worker readiness verification;
- real AWS adapter integration tests;
- positive and negative IAM tests;
- upload-to-index smoke test;
- index migration smoke test;
- failure, retry and DLQ verification;
- CloudWatch alarms and log-safety verification;
- documented operational runbook.

## Acceptance criteria for Phase 4A

- ADR status remains `Proposed` pending owner approval;
- GitHub Actions is the sole CI/CD mechanism;
- no AWS identifier or secret is committed;
- no deployable resource is created;
- Terraform and Helm boundaries match the application ports and runtime separation;
- Pod Identity includes verifiable IMDS and node-role isolation requirements;
- active S3 Vectors indexes are immutable, versioned and protected from routine destruction;
- plan and apply roles, environments, OIDC subjects and plan-integrity controls are explicit;
- S3, SQS, DynamoDB, probes and configuration traceability have testable contracts;
- the existing repository quality workflow remains green;
- Phase 4B does not start without explicit approval.