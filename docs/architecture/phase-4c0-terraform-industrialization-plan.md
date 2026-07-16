# Phase 4C-0 — Terraform industrialization migration plan

## Status

- Architecture decision: ADR-0012 `Accepted` through PR #11 on 2026-07-16.
- Migration plan: approved through PR #11 on 2026-07-16.
- Current implementation gate: 4C-0B1 only.
- Live ownership cutover: forbidden before 4C-0B3.

## Objective

Refactor the Phase 4B Terraform into reusable capability modules, a document-platform stack and small live environment roots before adding Phase 4C workload identity or Kubernetes resources.

This sub-lot changes architecture and repository structure only. It must not broaden the deployed AWS capability set.

## Phase gate

ADR-0012 and this migration plan were reviewed and approved through PR #11 on 2026-07-16.

B1 and B2 may prepare modules after that approval, but they must not change the active root, backend configuration, live resource ownership or state addresses. The first ownership change is the atomic B3 cutover.

## Baseline

The current development composition calls one local aggregate module:

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

No real AWS deployment was claimed by the merged Phase 4B pull request. The migration must nevertheless support both an empty state and an existing Phase 4B state. State classification is an explicit gate rather than an assumption.

## Mandatory state-discovery gate

Before B3 changes the live root or any resource address, an authorized operator produces an approved discovery package for the exact target environment.

### Non-sensitive discovery manifest

The manifest contains:

```text
environment
region directory selected for the live root
backend configuration identity or approved opaque reference
Terraform workspace
state lineage
state serial
Terraform version
provider-lock-file SHA-256 digest
complete terraform state list output
all S3 Vectors generation keys present in state
KMS mode: AWS-managed, module-managed customer key, or existing customer key
current root module address
manifest creation timestamp
manifest expiration timestamp
```

Real backend bucket names, role ARNs, state contents, resource attributes, secrets and sensitive outputs are not committed to the repository. An approved opaque backend reference may be used where the backend identity itself is restricted.

### Protected state backup

For a non-empty state, the operator captures before migration:

- an encrypted state backup obtained through the authorized backend path;
- a SHA-256 checksum;
- the source state lineage and serial;
- restricted access and the shortest practical retention;
- an audit reference recorded in the migration evidence.

The backup is never attached to a public pull request and is never logged by CI.

### Gate outcomes

The gate has only two valid outcomes:

1. **empty state** — lineage/serial and inventory evidence prove that no Phase 4B resource instances exist;
2. **existing Phase 4B state** — the complete inventory, vector keys, encryption mode and protected backup are available.

Missing, stale, contradictory or ambiguous evidence blocks B3. The saved migration plan must use the same state lineage and serial or explicitly fail and require a new discovery package.

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

`eks-workload-identity` and `document-observability` directories are created only when their implementation sub-lots begin. Phase 4C-0B creates only modules extracted from Phase 4B.

## Capability boundaries

### `document-encryption`

Owns:

- optional `aws_kms_key`;
- optional `aws_kms_alias`;
- key rotation and destruction-protection contract;
- stable effective-key output.

Does not own:

- service-specific KMS grants or workload IAM policies;
- provider or account discovery;
- environment naming.

The module preserves a Phase 4B customer-managed key when one exists. It is a compatibility mechanism for the structural migration, not a mandate that all capabilities permanently share one key.

### `document-storage`

Owns:

- S3 document bucket;
- public-access block;
- ownership controls;
- versioning;
- default encryption;
- temporary-upload lifecycle;
- TLS and encryption-enforcement bucket policy.

Receives final names, prefixes, tags and its own encryption configuration from the stack.

### `document-coordination`

Owns:

- DynamoDB control table;
- key schema;
- on-demand billing;
- point-in-time recovery;
- encryption;
- deletion protection.

Receives its own encryption configuration from the stack.

### `ingestion-messaging`

Owns:

- FIFO ingestion queue;
- FIFO dead-letter queue;
- redrive policy;
- redrive allow policy;
- encryption and messaging timing validations.

The stack supplies worker timing values required for cross-system validation. The messaging module validates messaging invariants and receives its own encryption configuration.

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
- Phase 4B-compatible shared encryption default;
- optional capability-specific encryption overrides in the stable interface;
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
  lease_ttl_seconds          = 900
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
  retained_contracts           = {}
  non_filterable_metadata_keys = []
}

encryption = {
  compatibility_default = {
    mode                 = "AES256"
    create_customer_key  = false
    existing_key_arn     = null
    deletion_window_days = 30
  }
  capability_overrides = {
    storage      = null
    coordination = null
    messaging    = null
    vector_store = null
  }
}
```

During 4C-0 every null override resolves to the Phase 4B-compatible default. A future key split supplies explicit overrides through a separately approved migration. Exact type names may change during implementation, but these boundaries must remain stable.

## Transitional ownership model

The extraction is split for reviewability, not for partial live deployment.

| Sub-lot | Active deployment root | Sole live resource owner | New modules in repository | Live address changes | Apply permitted |
|---|---|---|---|---|---|
| Before B1 | `infra/terraform/environments/dev` | `module.document_data_plane` | none | none | existing baseline only |
| B1 merged | unchanged | `module.document_data_plane` | encryption and storage, plus stack contracts | none | no structural apply |
| B2 merged | unchanged | `module.document_data_plane` | coordination and messaging added | none | no structural apply |
| B3 reviewed, before apply | new root configured against the exact existing backend | complete `module.document_platform` configuration with declarative moves from legacy addresses | vector store and complete stack | all moves declared atomically | plan only |
| B3 applied | `infra/terraform/live/dev/<region>/document-platform` | `module.document_platform` and its child capability modules | complete Phase 4B capability set | migration complete | approved atomic apply |

B1 and B2 must not add capability-module calls to the active live root. Their examples and stack fixtures use mocked or isolated test configurations only.

B3 removes the old aggregate module call in the same change that introduces the complete new stack call and every required `moved` block. The old and new declarations never coexist as independent owners of the same AWS resource.

## Current-to-target resource address map

The following base map is mandatory when a non-empty Phase 4B state exists.

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

The implementation may retain current resource labels inside leaf modules when that reduces migration risk. Any deviation from this table updates this plan before code review.

### Concrete vector-generation matrix

A collection placeholder is not an executable Terraform address. The state-discovery manifest lists every exact key returned by the state inventory. B3 then records one row and one static `moved` block per key.

Example discovery result:

| Current address | Target address |
|---|---|
| `module.document_data_plane.aws_s3vectors_index.documents["g001"]` | `module.document_platform.module.vector_store.aws_s3vectors_index.this["g001"]` |
| `module.document_data_plane.aws_s3vectors_index.documents["g002"]` | `module.document_platform.module.vector_store.aws_s3vectors_index.this["g002"]` |

Example blocks:

```hcl
moved {
  from = module.document_data_plane.aws_s3vectors_index.documents["g001"]
  to   = module.document_platform.module.vector_store.aws_s3vectors_index.this["g001"]
}

moved {
  from = module.document_data_plane.aws_s3vectors_index.documents["g002"]
  to   = module.document_platform.module.vector_store.aws_s3vectors_index.this["g002"]
}
```

No `<generation>` placeholder is accepted in implementation code. A generation present in the state but absent from `moved.tf`, or present in configuration but absent from the approved manifest, blocks the migration.

## Declarative state migration

The new live root contains explicit `moved` blocks for every applicable concrete address.

Example:

```hcl
moved {
  from = module.document_data_plane.aws_dynamodb_table.document_control
  to   = module.document_platform.module.coordination.aws_dynamodb_table.this
}
```

For conditional KMS resources, the configuration includes the static move while the discovery manifest records whether the instance exists. For vector indexes, one explicit block is generated and reviewed per discovered generation key.

No routine workflow runs `terraform state mv`. A manual state runbook is created only if Terraform cannot represent a required migration declaratively, and it requires separate approval.

## Live-root migration

The existing root is `infra/terraform/environments/dev`. The target root is `infra/terraform/live/dev/<region>/document-platform`.

Migration rules:

1. reuse the exact existing backend coordinates when a state exists;
2. verify the backend through the approved discovery reference rather than copying real values into the repository;
3. keep backend examples placeholder-only;
4. preserve the committed provider lock for the deployment root and verify its digest against the discovery manifest;
5. do not initialize a second state for the same resources;
6. do not run the old and new roots concurrently against the same backend;
7. archive the old root only after the new root produces the approved migration plan;
8. document the selected region directory in the B3 PR rather than hardcoding a universal project region;
9. bind the saved plan to the state lineage, serial, commit SHA, Terraform version and provider-lock digest;
10. invalidate and regenerate the plan whenever any binding changes.

For an empty state, B3 produces a create-only plan containing exactly the intended Phase 4B inventory and no Phase 4C-1 resource types.

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

### 4C-0B module and structural checks

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
- tests proving B1 and B2 do not change the active root;
- exact moved-address coverage against a controlled state fixture and, for B3, the approved discovery manifest.

### Plan JSON policies

The CI or protected plan path evaluates `terraform show -json` for controls expressed by the effective plan:

- no unapproved `delete`, `replace` or duplicate create action during migration;
- expected `previous_address` values for moved instances;
- S3 public-access controls, versioning, TLS policy and encryption attributes;
- DynamoDB deletion protection and point-in-time recovery attributes;
- FIFO and encryption attributes for queues and the intended redrive relationship;
- required tags;
- absence of unexpected resource types;
- absence of sensitive values in published summaries.

### Configuration-aware HCL policies

A pinned AST-aware HCL validation tool, not exact string matching, checks source-level contracts that plan JSON does not reliably expose:

- `lifecycle.prevent_destroy` on every durable resource instance required by the architecture;
- provider blocks absent from modules and stacks;
- backend blocks restricted to live roots;
- module sources local during migration and pinned when later promoted;
- forbidden static AWS credential variable and environment names;
- no hardcoded AWS account IDs, ARNs or backend identifiers;
- no broad lifecycle-protection bypass.

A migration behavior test additionally proves that an attempted destructive plan for protected fixture resources fails rather than silently deleting them.

### Migration-fixture and state-bound checks

The repository maintains a sanitized non-sensitive state fixture representing the Phase 4B address inventory. It contains no real identifiers or sensitive attributes.

Tests verify:

- every fixture address has exactly one destination;
- each vector generation key has one concrete move;
- no destination has multiple sources;
- the migrated plan has no unapproved replacement;
- compatibility outputs remain present;
- the B3 plan against a real non-empty state uses the approved lineage and serial;
- an inventory mismatch fails closed.

Temporary source-string checks are removed only after equivalent plan-aware or AST-aware controls are active.

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

### 4C-0B1 — Prepare encryption and storage

- create `document-encryption` and `document-storage`;
- add module tests and complete examples;
- introduce stack context, naming and stable configuration types;
- preserve capability-specific encryption inputs while resolving the compatibility default;
- keep `infra/terraform/environments/dev` and `module.document_data_plane` unchanged;
- add no production `moved` block;
- prove module equivalence through isolated tests only;
- do not apply a structural migration.

### 4C-0B2 — Prepare coordination and messaging

- create `document-coordination` and `ingestion-messaging`;
- add module tests and complete examples;
- preserve table, queue and timing contracts;
- extend stack fixtures without invoking the stack from the active root;
- keep the legacy aggregate module as sole live owner;
- add no production `moved` block;
- do not apply a structural migration.

### 4C-0B3 — Atomic ownership cutover

- require the approved state-discovery package and protected backup for a non-empty state;
- create `document-vector-store` and its tests;
- complete the stack and compatibility outputs;
- create the new live root against the exact approved backend;
- remove the aggregate module call and introduce the complete stack call in one change;
- add every static resource move and every concrete vector-generation move;
- generate a saved state-bound plan;
- prove zero unapproved destroy, replacement, duplicate creation or missing resource;
- apply only through a separately approved migration path;
- archive the old root after successful cutover verification.

### 4C-0C — Industrialized validation

- introduce plan JSON policies;
- introduce AST-aware HCL policies;
- add sanitized state-migration fixtures;
- replace broad `grep` gates only after equivalent controls pass;
- validate every example and stack;
- add documentation drift checks;
- automate reviewed dependency updates.

Each sub-lot is independently reviewed. No workload IAM, Helm, observability or deployment apply workflow is added during 4C-0.

## Acceptance criteria

4C-0 is complete only when:

- ADR-0012 is `Accepted`;
- the state-discovery outcome is recorded and approved before B3;
- a protected state backup exists for a non-empty state;
- all Phase 4B resources are owned by capability modules through the stack after B3;
- B1 and B2 leave the active root and live ownership unchanged;
- the live development root is minimal and contains no duplicated resource logic;
- module inputs contain no project-specific discovery or hardcoded AWS identifiers;
- naming and mandatory tags are resolved once at stack level;
- every module has tests and a complete example;
- stack tests cover cross-capability invariants;
- compatibility outputs are preserved;
- all resource address changes are declared with static addresses;
- every discovered vector generation has one exact `moved` block;
- a non-empty state migration shows zero unapproved destroy or replacement actions;
- an empty-state plan creates exactly the intended Phase 4B resource inventory;
- plan JSON policies cover effective actions and resource attributes;
- AST-aware HCL policies cover `prevent_destroy` and source-level constraints;
- migration fixtures prove address coverage and fail closed on drift;
- Terraform formatting, validation and tests pass;
- existing Python lint, strict type checking and tests remain green;
- no Phase 4C-1 capability has been introduced.

## Rollback

Before any B3 apply, rollback is a source-code revert and invalidation of the saved plan.

After a state-preserving apply, rollback does not recreate the old module tree blindly. It requires a reviewed reverse address migration or forward correction. Durable resources remain protected by deletion protection and `prevent_destroy`.

No rollback procedure may disable protection merely to simplify module movement. The protected pre-migration backup is a recovery control, not permission to overwrite state without a reviewed incident procedure.

## Deferred scope

- API and worker IAM roles;
- EKS Pod Identity associations;
- IMDS isolation implementation;
- Helm chart and Kubernetes workloads;
- document-specific observability resources;
- GitHub OIDC plan/apply roles and workflows;
- real AWS deployment;
- positive and negative AWS integration tests;
- capability-specific KMS key migration;
- frontend, durable deletion, retrieval and custom agents.
