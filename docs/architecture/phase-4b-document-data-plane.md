# Phase 4B — Terraform document data plane

## Status

Implemented on `feature/phase-4b-document-data-plane-corrections` for review. This increment creates deployable Terraform definitions but does not plan or apply them against an AWS account.

ADR-0011 remains the governing architecture decision. This implementation does not change the accepted boundaries: workload IAM, EKS Pod Identity, Helm, deployment OIDC and real AWS integration remain later phases.

## Delivered layout

```text
infra/terraform/
├── modules/
│   └── document-data-plane/
│       ├── versions.tf
│       ├── variables.tf
│       ├── vector_migration_variables.tf
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
        ├── vector_migration_variables.tf
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

### S3 document storage

The document bucket is private, bucket-owner enforced, versioned, encrypted, TLS-only and protected by `prevent_destroy` with `force_destroy = false`.

The temporary lifecycle applies only to:

```text
<document_source_prefix>/uploads/
```

It expires both current and noncurrent temporary object versions after exactly one day. This prevents versioned temporary uploads from remaining indefinitely after expiration or application deletion. Incomplete multipart uploads are aborted independently.

Terraform rejects a `document_index_prefix` equal to or below the temporary-upload prefix. Durable chunks and vector manifests therefore cannot be selected by the one-day lifecycle rule.

### FIFO ingestion queue

The module creates one encrypted FIFO ingestion queue, one encrypted FIFO dead-letter queue, an explicit redrive policy, a restricted redrive allow policy, long polling and configurable retention.

Terraform rejects unsafe timing when:

- visibility timeout is lower than the ingestion lease TTL;
- heartbeat is greater than half the lease TTL;
- heartbeat is greater than half the visibility timeout;
- DLQ retention is shorter than source-queue retention.

### S3 Vectors

The vector bucket and every index use native AWS provider resources and are protected from routine destruction.

Indexes are managed as a generation-keyed collection. The active contract is defined by:

- embedding profile alias;
- embedding profile revision;
- vector dimension;
- distance metric;
- active index generation;
- encryption revision.

Previously active contracts are supplied through `retained_vector_index_contracts`. Terraform can therefore manage the previous and next generations concurrently while application outputs select only the active generation.

Migration sequence:

1. add the current contract to `retained_vector_index_contracts`;
2. configure the new active contract and generation;
3. create both indexes in the same plan;
4. re-index and verify coverage;
5. switch application configuration to the new active output;
6. observe the stabilization period;
7. retire the old generation in a separate reviewed change.

The required authorization metadata keys remain filterable:

```text
tenantId
classification
allowedRoles
generationId
```

### KMS

Two encryption modes are supported:

- `AES256`, with no customer-managed key input;
- `aws:kms`, with exactly one module-managed key or existing key ARN.

A module-managed key enables rotation and uses `prevent_destroy`. Phase 4C will define workload key-use permissions.

## Naming and external values

No account ID, ARN, region, remote-state bucket or state key is hardcoded.

Global names are derived from typed inputs plus the current AWS account identity and configured region. Backend and environment example files contain placeholders only.

## Application output mapping

The module exposes individual resource outputs and an `application_runtime_settings` map for later Helm composition. Active runtime settings point only to the active vector generation. Separate maps expose all retained and active vector index names and ARNs for migration operations and later IAM policy composition.

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
static destruction, lifecycle, prefix, migration, redrive and hardcoded-ARN guardrails
```

The Terraform test suite covers:

- DynamoDB `pk`/`sk`, on-demand capacity and deletion protection;
- S3 public blocking, versioning and current/noncurrent temporary expiration;
- rejection of a chunk prefix under temporary uploads;
- FIFO queue and DLQ selection;
- KMS and AWS-managed encryption contracts;
- rejection of unsafe SQS and lease timing;
- rejection of non-filterable authorization metadata;
- concurrent planning of retained and active vector index generations;
- active runtime output selection during migration.

## Deferred work

This increment intentionally does not implement:

- API and worker IAM roles;
- EKS Pod Identity associations or IMDS isolation controls;
- Helm resources and workload probes;
- document observability resources and alarms;
- GitHub OIDC plan/apply roles or workflows;
- AWS deployment or integration testing;
- frontend, durable deletion, retrieval or custom agents.

Phase 4C must not start until this corrected Phase 4B pull request is approved and merged.
