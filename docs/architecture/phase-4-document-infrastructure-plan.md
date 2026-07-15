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
  │     └── encrypted DLQ
  ├── S3 Vectors vector bucket and index
  ├── optional KMS keys
  ├── CloudWatch logs, metrics and alarms
  └── EKS
        ├── API Deployment
        │     └── ia-agents-api service account / API IAM role
        └── Worker Deployment
              └── ia-agents-worker service account / worker IAM role
```

The API and worker use the same immutable container image but different commands, service accounts, scaling rules and IAM permissions.

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

Environment examples contain placeholders only. Real backend values, role ARNs, account IDs, names and secrets are supplied through GitHub environment configuration or local uncommitted files.

## Module contracts

### `document-data-plane`

Inputs include:

- resource-name prefix;
- AWS region;
- environment name;
- common tags;
- source and chunk prefixes;
- temporary-upload prefix and lifecycle duration;
- queue visibility timeout and redrive count;
- embedding vector dimension and distance metric;
- encryption mode and optional KMS key inputs;
- deletion protection and retention controls.

Outputs include:

- DynamoDB table name and ARN;
- document bucket name and ARN;
- lifecycle rule identifier;
- ingestion queue URL and ARN;
- DLQ URL and ARN;
- S3 Vectors bucket and index names and ARNs;
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

## IAM separation

### API role

The API role is restricted to:

- document, job and submission-lease records in the configured DynamoDB table;
- temporary upload creation and metadata inspection;
- conditional copy to the immutable source prefix;
- temporary-object deletion;
- task submission to the ingestion queue;
- readiness reads for configured resources.

It must not be able to invoke Bedrock embeddings or publish vectors.

### Worker role

The worker role is restricted to:

- receiving, extending and acknowledging ingestion messages;
- job, document, lease and generation records;
- immutable source reads;
- chunk writes and generation cleanup within the configured prefix;
- configured Bedrock embedding invocation;
- S3 Vectors index writes and readiness reads.

It must not be able to mint upload URLs or administer unrelated platform resources.

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
- separate service accounts with no automatic token mounting unless required;
- startup, liveness and readiness probes;
- CPU and memory requests and limits;
- non-root execution, read-only root filesystem and dropped Linux capabilities;
- configurable replica counts and autoscaling;
- disruption budgets;
- topology spread and anti-affinity options;
- namespace-scoped network policies;
- ConfigMap values containing no secrets;
- secret references resolved through the platform secret-injection mechanism.

## GitHub Actions design

### Existing workflow

`.github/workflows/quality.yml` remains unchanged as the Python lint, typecheck and test gate.

### Infrastructure validation workflow

A new workflow runs on pull requests changing `infra/**`, infrastructure ADRs or infrastructure workflows. It requires no AWS credentials and performs:

- `terraform fmt -check`;
- provider initialization without remote backend access;
- `terraform validate` for modules and environment composition;
- `terraform test`;
- static security and policy checks;
- `helm lint`;
- deterministic `helm template` rendering;
- Kubernetes manifest schema validation;
- workflow syntax validation.

Tool versions and third-party action revisions are pinned.

### Plan workflow

The plan workflow:

- is manually dispatched or called through a protected reusable workflow;
- selects a GitHub deployment environment;
- requests `id-token: write` only for the AWS-assuming job;
- assumes a read-oriented Terraform planning role through GitHub OIDC;
- initializes the configured remote backend;
- creates a plan from the exact commit SHA;
- uploads the plan and a human-readable summary as artifacts;
- never applies changes.

### Apply workflow

The apply workflow:

- uses a protected GitHub deployment environment requiring approval;
- assumes a separate apply role through GitHub OIDC;
- verifies that the plan artifact belongs to the selected commit and environment;
- prevents concurrent applies to the same environment;
- applies only the reviewed plan;
- reports failures without masking partial changes.

No workflow stores long-lived AWS credentials.

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
- development composition;
- module tests and static validation;
- infrastructure validation workflow foundation;
- updated ADR consequences where implementation constraints differ.

### Phase 4C — Workload identity and Helm

Deliverables:

- API and worker IAM roles;
- EKS Pod Identity associations;
- Helm chart with API and worker deployments;
- manifest and chart tests;
- immutable image-value contract.

### Phase 4D — GitHub Actions plan and apply

Deliverables:

- OIDC plan workflow;
- protected apply workflow;
- separate plan and apply role contracts;
- remote-state input contract;
- deployment concurrency and artifact-integrity controls.

### Phase 4E — Development deployment and integration validation

Deliverables:

- deployed `dev` resources;
- API and worker readiness verification;
- real AWS adapter integration tests;
- upload-to-index smoke test;
- failure, retry and DLQ verification;
- CloudWatch alarms and log-safety verification;
- documented operational runbook.

## Acceptance criteria for Phase 4A

- ADR status remains `Proposed` pending owner approval;
- no GitLab CI references are introduced;
- no AWS identifier or secret is committed;
- no deployable resource is created;
- Terraform and Helm boundaries match the application ports and runtime separation;
- GitHub Actions is the sole CI/CD mechanism;
- the existing repository quality workflow remains green;
- Phase 4B does not start without explicit approval.
