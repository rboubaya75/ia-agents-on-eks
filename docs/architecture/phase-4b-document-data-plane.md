# Phase 4B — Terraform document data plane

## Status

Implemented on `feature/phase-4b-document-data-plane` for review. This increment creates deployable Terraform definitions but does not plan or apply them against an AWS account.

ADR-0011 remains the governing architecture decision. This implementation does not change the accepted boundaries: workload IAM, EKS Pod Identity, Helm, deployment OIDC and real AWS integration remain later phases.

## Delivered layout

```text
infra/terraform/
├── modules/
│   └── document-data-plane/
│       ├── versions.tf
│       ├── variables.tf
│       ├── locals.tf
│       ├── kms.tf
│       ├── dynamodb.tf
│       ├── s3.tf
│       ├── sqs.tf
│       ├── s3vectors.tf
│       ├── outputs.tf
│       ├── README.md
│       └── tests/
│           └── document_data_plane.tftest.hcl
└── environments/
    └── dev/
        ├── versions.tf
        ├── providers.tf
        ├── variables.tf
        ├── main.tf
        ├── outputs.tf
        ├── .terraform.lock.hcl
        ├── backend.hcl.example
        └── terraform.tfvars.example
```

Terraform is pinned to `1.15.8`. The AWS provider is pinned and locked to `6.55.0`.

## Resource contract

### DynamoDB

The module creates one on-demand control table matching the merged adapters:

```text
partition key: pk (String)
sort key:      sk (String)
```

The table enables server-side encryption, point-in-time recovery and deletion protection. Terraform also applies `prevent_destroy`; table retirement therefore requires a separate reviewed change.

TTL is not enabled. The current application does not require DynamoDB TTL for its durable document, job, generation and lease records.

### S3 document storage

The document bucket is:

- private through all four S3 public-access-block controls;
- bucket-owner enforced;
- versioned;
- encrypted with AWS-managed AES-256 or a configured customer-managed KMS key;
- protected by `prevent_destroy` and `force_destroy = false`;
- restricted to TLS requests;
- configured to reject uploads that omit or contradict the selected encryption contract.

The temporary lifecycle rule applies only to:

```text
<document_source_prefix>/uploads/
```

It expires temporary uploads after exactly one day, matching the application readiness check. It does not expire immutable sources or active chunk generations. Incomplete multipart uploads are aborted independently.

### FIFO ingestion queue

The module creates:

- one encrypted FIFO ingestion queue;
- one encrypted FIFO dead-letter queue;
- an explicit redrive policy;
- a redrive allow policy restricted to the ingestion queue;
- long polling and configurable retention.

Terraform rejects unsafe worker timing when:

- visibility timeout is lower than the ingestion lease TTL;
- heartbeat is greater than half the lease TTL;
- heartbeat is greater than half the visibility timeout;
- DLQ retention is shorter than source-queue retention.

The application continues to supply the deterministic message group and deduplication identifiers; content-based deduplication remains disabled.

### S3 Vectors

The vector bucket and index use native AWS provider resources. Both are protected from routine destruction.

The index name deterministically represents an immutable contract containing:

- embedding profile alias;
- embedding profile revision;
- vector dimension;
- distance metric;
- explicit index generation;
- encryption mode and encryption revision.

Changing that contract produces a distinct index name. It does not mutate or replace the active index in place.

The metadata keys required for tenant and authorization filtering remain filterable:

```text
tenantId
classification
allowedRoles
generationId
```

Terraform rejects configurations that mark one of those keys as non-filterable.

### KMS

Two encryption modes are supported:

- `AES256`, with no customer-managed key input;
- `aws:kms`, with exactly one module-managed key or existing key ARN.

A module-managed key enables rotation and uses `prevent_destroy`. Phase 4C will define the API and worker key-use permissions; this phase does not create workload IAM.

## Naming and AWS identifiers

No account ID, ARN, region or resource name is hardcoded.

Global names are derived from typed inputs plus the trusted current AWS account identity and configured region. The committed examples contain placeholders only. The reusable module rejects fixed AWS ARNs through the infrastructure validation workflow.

## Application output mapping

The module exposes individual resource outputs and an `application_runtime_settings` map for later Helm composition. It covers the merged settings for:

- document control table;
- document bucket and prefixes;
- upload lifecycle rule ID;
- maximum source size;
- KMS key identifier when applicable;
- ingestion queue URL and timing values;
- S3 Vectors bucket and immutable index;
- embedding profile alias, revision and dimensions.

Feature activation, the resolved Bedrock model ID and pipeline version remain deployment inputs. They are not owned by this data-plane module.

## Development composition

`infra/terraform/environments/dev/` composes the reusable module with:

- a partial S3 backend;
- an AWS provider configured from an input region;
- example backend and environment values containing placeholders only;
- a committed provider lock file;
- outputs intended for Phase 4C workload identity and Helm.

The remote-state bucket, state role and real environment values are prerequisites. They are not created or committed in this increment.

## Automated validation

`.github/workflows/infrastructure-validation.yml` runs without AWS credentials and uses minimal `contents: read` permissions. Third-party actions are pinned to immutable commit SHAs.

The workflow executes:

```text
terraform fmt -check -recursive
module init without backend
module validate
terraform test
dev init without backend and with lockfile=readonly
dev validate
provider-lock integrity check
static destruction, redrive and hardcoded-ARN guardrails
```

The Terraform test suite covers:

- the DynamoDB `pk`/`sk` schema and on-demand mode;
- deletion protection;
- S3 public blocking, versioning and exact temporary lifecycle;
- FIFO queue and DLQ selection;
- vector dimensions, metric, generation and destruction controls;
- the customer-managed KMS path;
- rejection of incomplete KMS configuration;
- rejection of unsafe SQS/lease timing;
- rejection of non-filterable authorization metadata;
- creation of a distinct vector index for a new embedding revision and generation.

## Deferred work

This increment intentionally does not implement:

- API and worker IAM roles;
- EKS Pod Identity associations or IMDS isolation controls;
- Helm resources and workload probes;
- document observability resources and alarms;
- GitHub OIDC plan/apply roles or workflows;
- AWS deployment or integration testing;
- frontend, durable deletion, retrieval or custom agents.

Those items remain subject to their existing phase gates. Phase 4C must not start until this Phase 4B pull request is approved and merged.
