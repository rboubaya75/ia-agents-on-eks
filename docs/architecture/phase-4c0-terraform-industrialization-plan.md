# Phase 4C-0 — Terraform industrialization migration plan

## Objective

Refactor the Phase 4B Terraform into reusable capability modules, a document-platform stack and small live environment roots before adding Phase 4C workload identity or Kubernetes resources.

This sub-lot changes architecture and repository structure only. It must not broaden the deployed AWS capability set.

## Phase gate

This plan implements the architecture proposed by ADR-0012. ADR-0012 remains `Proposed` until review approval.

The structural refactor must not start until both ADR-0012 and this migration plan are approved.

## Baseline

The current development composition calls one local module:

```text
infra/terraform/environments/dev
└── module.document_data_plane
    ├── DynamoDB control table
    ├── S3 document bucket and controls
    ├── FIFO SQS ingestion queue and DLQ
    ├── optional KMS key and alias
    └── S3 Vectors bucket and index generations
```

The module contains valid durability and security controls, including:

- DynamoDB deletion protection, point-in-time recovery and encryption;
- S3 public-access blocking, versioning, TLS-only policy and temporary-upload lifecycle;
- encrypted FIFO queues and bounded redrive;
- protected vector bucket and index generations;
- optional rotating KMS key;
- `prevent_destroy` on durable resources.

The refactor preserves those controls. It does not reinterpret Phase 4B as disposable work.

No real AWS deployment has been claimed by the merged Phase 4B pull request. Before any migration plan is accepted as state-safe, the operator must determine whether a remote development state exists. The implementation must support both an empty state and a previously initialized state.

## Target repository structure

```text
infra/terraform/
├── modules/
│   ├── document-encryption/
│   │   ├── README.md
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   ├── outputs.tf
│   │   ├── versions.tf
│   │   ├── tests/
│   │   └── examples/complete/
│   ├── document-storage/
│   ├── document-coordination/
│   ├── ingestion-messaging/
│   ├── document-vector-store/
│   ├── eks-workload-identity/
│   └── document-observability/
├── stacks/
│   └── document-platform/
│       ├── README.md
│       ├── main.tf
│       ├── context.tf
│       ├── variables.tf
│       ├── outputs.tf
│       ├── checks.tf
│       ├── versions.tf
│       └── tests/
├── live/
│   └── dev/
│       └── <region>/
│           └── document-platform/
│               ├── backend.hcl.example
│               ├── main.tf
│               ├── providers.tf
│               ├── variables.tf
│               ├── outputs.tf
│               ├── terraform.tfvars.example
│               ├── versions.tf
│               └── moved.tf
├── policies/
│   └── terraform/
└── tests/
    └── terraform/
```

`eks-workload-identity` and `document-observability` directories may be created as documented placeholders only after their implementation sub-lots begin. Phase 4C-0B creates only the modules extracted from Phase 4B.

## Capability boundaries

### `document-encryption`

Owns:

- optional `aws_kms_key`;
- optional `aws_kms_alias`;
- key rotation and deletion protection contract;
- stable effective-key output.

Does not own:

- service-specific KMS grants or workload IAM policies;
- provider or account discovery;
- environment naming.

### `document-storage`

Owns:

- S3 document bucket;
- public-access block;
- ownership controls;
- versioning;
- default encryption;
- temporary-upload lifecycle;
- TLS and encryption-enforcement bucket policy.

Receives final names, prefixes, tags and encryption configuration from the stack.

### `document-coordination`

Owns:

- DynamoDB control table;
- key schema;
- on-demand billing;
- point-in-time recovery;
- encryption;
- deletion protection.

### `ingestion-messaging`

Owns:

- FIFO ingestion queue;
- FIFO dead-letter queue;
- redrive policy;
- redrive allow policy;
- encryption and timing validations.

The stack supplies worker timing values required for cross-system validation. The messaging module validates only messaging invariants.

### `document-vector-store`

Owns:

- S3 Vectors bucket;
- active and retained immutable index generations;
- metadata filterability contract;
- vector encryption contract;
- index migration outputs.

Embedding profile identity remains a versioned infrastructure contract for index creation, but feature activation and runtime model selection remain outside the module.

### `document-platform` stack

Owns composition only:

- deployment context validation;
- deterministic names and mandatory tags;
- optional encryption module selection;
- capability-module wiring;
- cross-capability checks;
- compatibility outputs matching Phase 4B consumers;
- typed runtime infrastructure settings.

The stack does not configure providers or a backend.

## Deployment context contract

The live root passes one typed context object to the stack.

```hcl
context = {
  workload            = "ia-agents"
  component           = "document-platform"
  environment         = "dev"
  region              = var.aws_region
  owner               = var.owner
  cost_center         = var.cost_center
  data_classification = var.data_classification
  additional_tags     = var.tags
}
```

Actual environment values remain external or placeholder-only in the repository.

The stack creates final names once and passes them to modules. Leaf modules must not embed `ia-agents-on-eks`, discover the account solely for naming, or derive competing name formats.

## Configuration contracts

The stack groups environment inputs by capability.

```hcl
storage = {
  source_prefix                          = "documents"
  index_prefix                           = "rag"
  temporary_upload_expiration_days       = 1
  abort_incomplete_multipart_upload_days = 1
}

messaging = {
  visibility_timeout_seconds = 900
  message_retention_seconds  = 345600
  dlq_retention_seconds      = 1209600
  max_receive_count          = 5
  receive_wait_time_seconds  = 20
}

worker_runtime_contract = {
  lease_ttl_seconds        = 900
  heartbeat_interval_seconds = 60
}

vector_store = {
  active_generation = "g001"
  active_contract = {
    embedding_profile_alias    = "..."
    embedding_profile_revision = "..."
    dimensions                 = 1024
    distance_metric            = "cosine"
    encryption_revision        = "enc-v1"
  }
  retained_contracts            = {}
  non_filterable_metadata_keys  = []
}

encryption = {
  mode                 = "AES256"
  create_customer_key  = false
  existing_key_arn     = null
  deletion_window_days = 30
}
```

Exact type names may change during implementation, but the boundaries must remain stable.

## Current-to-target resource address map

The following address map is mandatory when a non-empty Phase 4B state exists.

| Current address | Target address |
|---|---|
| `module.document_data_plane.aws_kms_key.document[0]` | `module.document_platform.module.encryption.aws_kms_key.this[0]` |
| `module.document_data_plane.aws_kms_alias.document[0]` | `module.document_platform.module.encryption.aws_kms_alias.this[0]` |
| `module.document_data_plane.aws_dynamodb_table.document_control` | `module.document_platform.module.coordination.aws_dynamodb_table.this` |
| `module.document_data_plane.aws_s3_bucket.documents` | `module.document_platform.module.storage.aws_s3_bucket.this` |
| `module.document_data_plane.aws_s3_bucket_public_access_block.documents` | `module.document_platform.module.storage.aws_s3_bucket_public_access_block.this` |
| `module.document_data_plane.aws_s3_bucket_ownership_controls.documents` | `module.document_platform.module.storage.aws_s3_bucket_ownership_controls.this` |
| `module.document_data_plane.aws_s3_bucket_versioning.documents` | `module.document_platform.module.storage.aws_s3_bucket_versioning.this` |
| `module.document_data_plane.aws_s3_bucket_server_side_encryption_configuration.documents` | `module.document_platform.module.storage.aws_s3_bucket_server_side_encryption_configuration.this` |
| `module.document_data_plane.aws_s3_bucket_lifecycle_configuration.documents` | `module.document_platform.module.storage.aws_s3_bucket_lifecycle_configuration.this` |
| `module.document_data_plane.aws_s3_bucket_policy.documents` | `module.document_platform.module.storage.aws_s3_bucket_policy.this` |
| `module.document_data_plane.aws_sqs_queue.ingestion_dlq` | `module.document_platform.module.messaging.aws_sqs_queue.dlq` |
| `module.document_data_plane.aws_sqs_queue.ingestion` | `module.document_platform.module.messaging.aws_sqs_queue.ingestion` |
| `module.document_data_plane.aws_sqs_queue_redrive_allow_policy.ingestion_dlq` | `module.document_platform.module.messaging.aws_sqs_queue_redrive_allow_policy.dlq` |
| `module.document_data_plane.aws_s3vectors_vector_bucket.documents` | `module.document_platform.module.vector_store.aws_s3vectors_vector_bucket.this` |
| `module.document_data_plane.aws_s3vectors_index.documents[<generation>]` | `module.document_platform.module.vector_store.aws_s3vectors_index.this[<generation>]` |

The implementation may retain current resource labels inside leaf modules if that reduces migration risk. Any deviation from this table must update the plan before code review.

## Declarative state migration

The new live root contains explicit `moved` blocks for every applicable address.

Example:

```hcl
moved {
  from = module.document_data_plane.aws_dynamodb_table.document_control
  to   = module.document_platform.module.coordination.aws_dynamodb_table.this
}
```

For conditional KMS resources, moved blocks remain valid even when the instances are absent. For vector indexes, the migration must preserve every retained generation key.

The old module call and new module call must not coexist as independent owners of the same AWS resources.

No routine workflow runs `terraform state mv`. A manual state runbook is created only if Terraform cannot represent a required migration declaratively.

## Live-root migration

The existing root is `infra/terraform/environments/dev`. The target root is `infra/terraform/live/dev/<region>/document-platform`.

Migration rules:

1. reuse the exact existing backend coordinates when a state exists;
2. copy no real backend identifiers into the repository;
3. keep backend examples placeholder-only;
4. preserve the committed provider lock for the deployment root;
5. do not initialize a second state for the same resources;
6. archive the old root only after the new root produces the expected plan;
7. document the selected region directory in the deployment PR rather than hardcoding a universal project region.

If the current state is confirmed empty, the state-move checks still remain in tests to prevent future accidental recreation during branch drift.

## Compatibility outputs

The new stack and live root retain Phase 4B output contracts needed by the application and later phases, including:

- DynamoDB table name and ARN;
- document bucket name and ARN;
- source, upload and chunk prefixes;
- ingestion queue URL and ARN;
- dead-letter queue URL and ARN;
- vector bucket name and ARN;
- active vector index name and ARN;
- all retained vector index contracts;
- effective KMS key ARN when applicable;
- typed application runtime settings.

Output names may be deprecated only through a documented compatibility period.

## Validation model

### 4C-0B structural checks

Required checks:

- `terraform fmt -check -recursive`;
- `terraform init -backend=false` for every module, stack and complete example;
- `terraform validate` for every module, stack, example and live root;
- `terraform test` for every capability module and the stack;
- provider-lock integrity for the live root;
- no new AWS resource types beyond the Phase 4B inventory;
- no hardcoded AWS account IDs, ARNs, backend values or credentials;
- no provider blocks in modules or stacks;
- no backend blocks outside live roots;
- compatibility output tests;
- moved-address coverage tests.

### 4C-0C policy checks

The CI pipeline generates a Terraform plan and evaluates its JSON representation with policy as code.

Required policies:

- durable resources cannot be deleted or replaced in the structural migration;
- S3 public-access controls are complete;
- S3 versioning, TLS-only access and encryption are enabled;
- DynamoDB deletion protection and point-in-time recovery are enabled;
- queues are FIFO, encrypted and connected to the intended DLQ;
- vector resources retain destruction protection;
- required tags are present;
- IAM wildcard rules are enforced when identity modules are introduced;
- secrets and long-lived credentials are absent.

Temporary source-string checks are removed only after equivalent plan policies and Terraform tests are active.

## Documentation automation

Each reusable module receives generated input and output documentation. CI fails when generated documentation differs from committed documentation.

Every module README includes:

- purpose and non-goals;
- ownership and lifecycle boundary;
- input/output contract;
- complete example;
- destruction behavior;
- migration notes;
- contract version.

## Implementation sequence

### 4C-0A — Architecture

Changed files:

- `docs/adr/0012-terraform-industrialization-model.md`;
- `docs/architecture/phase-4c0-terraform-industrialization-plan.md`.

No Terraform code changes.

### 4C-0B1 — Extract encryption and storage

- create `document-encryption` and `document-storage`;
- add unit and complete-example tests;
- introduce stack context and naming;
- add corresponding moved blocks;
- prove no storage or key replacement.

### 4C-0B2 — Extract coordination and messaging

- create `document-coordination` and `ingestion-messaging`;
- preserve table and queue contracts;
- add moved blocks and tests;
- prove no table or queue replacement.

### 4C-0B3 — Extract vector store and complete stack

- create `document-vector-store`;
- preserve active and retained generation keys;
- complete stack and compatibility outputs;
- migrate the development root;
- remove the old aggregate module only after equivalence is proven.

### 4C-0C — Industrialized validation

- introduce plan JSON policy tests;
- replace broad `grep` gates;
- validate every example and stack;
- add documentation drift checks;
- automate reviewed dependency updates.

Each sub-lot is independently reviewed. No workload IAM, Helm, observability or deployment apply workflow is added during 4C-0.

## Acceptance criteria

4C-0 is complete only when:

- ADR-0012 is `Accepted`;
- all Phase 4B resources are owned by capability modules through the stack;
- the live development root is minimal and contains no duplicated resource logic;
- module inputs contain no project-specific discovery or hardcoded AWS identifiers;
- naming and mandatory tags are resolved once at stack level;
- every module has tests and a complete example;
- stack tests cover cross-capability invariants;
- compatibility outputs are preserved;
- all resource address changes are declared;
- a non-empty state migration, when applicable, shows zero unapproved destroy or replacement actions;
- an empty-state plan creates exactly the intended Phase 4B resource inventory;
- plan JSON policies replace broad source-string checks;
- Terraform formatting, validation and tests pass;
- existing Python lint, strict type checking and tests remain green;
- no Phase 4C-1 capability has been introduced.

## Rollback

Before any apply, rollback is a source-code revert.

After a state-preserving apply, rollback does not recreate the old module tree blindly. It requires a reviewed reverse address migration or forward correction. Durable resources remain protected by deletion protection and `prevent_destroy`.

No rollback procedure may disable protection merely to simplify module movement.

## Deferred scope

- API and worker IAM roles;
- EKS Pod Identity associations;
- IMDS isolation implementation;
- Helm chart and Kubernetes workloads;
- document-specific observability resources;
- GitHub OIDC plan/apply roles and workflows;
- real AWS deployment;
- positive and negative AWS integration tests;
- frontend, durable deletion, retrieval and custom agents.