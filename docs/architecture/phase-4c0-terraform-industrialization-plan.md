# Phase 4C-0 — Terraform industrialization migration plan

## Status

- Architecture decision: ADR-0012 `Accepted` after review and merge of PR #11.
- Current implementation gate: 4C-0B1 only.
- Live ownership cutover: forbidden before 4C-0B3.

## Objective

Refactor the Phase 4B Terraform into reusable capability modules, a `document-platform` stack and small live environment roots before adding Phase 4C workload identity or Kubernetes resources.

This work changes architecture and repository structure only. It must not broaden the deployed AWS capability set.

## Phase gate

ADR-0012 and this migration plan are approved for preparatory implementation.

B1 and B2 may add and test modules, examples and stack contracts, but they must not change the active root, resource ownership, backend configuration or state addresses. The first ownership change is the atomic B3 cutover.

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

The refactor preserves the existing durability and security controls. It does not reinterpret Phase 4B resources as disposable.

No real AWS deployment was claimed by the merged Phase 4B pull request. The migration must nevertheless support both an empty state and an existing Phase 4B state. State classification is an explicit gate rather than an assumption.

## Mandatory state-discovery gate

Before B3 changes the live root or any resource address, an authorized operator produces an approved discovery package for the exact target environment.

The non-sensitive manifest contains:

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

Real backend bucket names, role ARNs, state contents, resource attributes, secrets and sensitive outputs are not committed to the repository.

For a non-empty state, the operator captures an encrypted state backup, its SHA-256 checksum, lineage and serial, restricted-access evidence and a migration audit reference. The backup is never attached to a public pull request or logged by CI.

The only valid outcomes are:

1. **empty state** — evidence proves that no Phase 4B resource instances exist;
2. **existing Phase 4B state** — complete inventory, vector keys, encryption mode and protected backup are available.

Missing, stale, contradictory or ambiguous evidence blocks B3. A saved migration plan must use the same lineage and serial or fail and require a new discovery package.

## Target structure

```text
infra/terraform/
├── modules/
│   ├── document-encryption/
│   ├── document-storage/
│   ├── document-coordination/
│   ├── ingestion-messaging/
│   ├── document-vector-store/
│   ├── eks-workload-identity/
│   └── document-observability/
├── stacks/
│   └── document-platform/
├── live/
│   └── <environment>/<region>/document-platform/
├── policies/terraform/
└── tests/terraform/
```

Workload identity and observability directories are created only when their implementation phases begin. Phase 4C-0B creates only capabilities extracted from Phase 4B.

## Capability boundaries

### `document-encryption`

Owns the optional KMS key, optional alias, rotation, destruction protection and stable effective-key output. It does not own service grants, workload IAM, provider discovery or environment naming.

The Phase 4B compatibility key remains the singleton `module.encryption`. Capability-specific overrides use separate module instances and do not split a live key during B1 or B2.

### `document-storage`

Owns the S3 document bucket, public-access block, ownership controls, versioning, default encryption, temporary-upload lifecycle and TLS/encryption-enforcement bucket policy.

It receives final names, prefixes, tags and its encryption contract from the stack.

### `document-coordination`

Owns the DynamoDB control table, key schema, on-demand billing, point-in-time recovery, encryption and deletion protection.

### `ingestion-messaging`

Owns the FIFO ingestion queue, FIFO DLQ, redrive policy, redrive allow policy, encryption and messaging timing validations.

### `document-vector-store`

Owns the S3 Vectors bucket, active and retained immutable index generations, metadata filterability contract, vector encryption contract and migration outputs.

### `document-platform` stack

Owns composition only: trusted deployment-context validation, deterministic names, mandatory tags, compatibility encryption defaults, optional per-capability overrides, module wiring, cross-capability checks, compatibility outputs and typed runtime infrastructure settings.

The stack contains no provider or backend block.

## Deployment context

The live root supplies trusted, non-secret context including workload, component, environment, region, account ID, owner, cost center, data classification and additional tags.

The stack resolves final names and mandatory tags. Additional tags cannot redefine reserved platform keys. Leaf modules do not discover account context for naming and do not embed project-specific identifiers.

## Validation model

Validation is layered:

1. `terraform fmt`, initialization and validation;
2. Terraform tests for positive and negative module contracts;
3. executable complete examples;
4. stack tests for naming, encryption and tags;
5. plan JSON policies for effective actions and values;
6. HCL-aware policies for lifecycle and source constraints;
7. controlled migration fixtures and approved state evidence.

During B1, temporary static checks remain narrow regression guards. They do not replace the plan-aware and HCL-aware controls scheduled for 4C-0C.

## Delivery sequence

### 4C-0B1 — preparatory encryption and storage

- create and test `document-encryption` and `document-storage`;
- introduce the initial stack contract;
- preserve Phase 4B names, aliases, prefixes and controls;
- keep `module.document_data_plane` as the sole live owner;
- prohibit live roots, backends, `moved` blocks and partial apply paths.

### 4C-0B2 — preparatory coordination and messaging

- create and test DynamoDB and SQS capability modules;
- extend the stack contract;
- keep the aggregate module as sole live owner.

### 4C-0B3 — atomic structural cutover

- complete the vector-store module and stack;
- introduce the live root;
- remove the aggregate module call in the same change;
- add explicit static `moved` blocks for every existing resource instance;
- add one concrete move for every discovered `for_each` key;
- preserve compatibility outputs;
- require a plan with zero unapproved delete, replacement, duplicate or missing address.

### 4C-0C — industrialized validation

- implement plan JSON policies;
- implement HCL-aware policies;
- implement migration fixtures;
- implement generated-documentation drift checks.

## Rollback and failure rules

B1 and B2 are repository-only preparatory changes and are not applied as partial migrations.

B3 is blocked when state evidence is absent or stale, the provider lock differs, a generation key is missing, an address differs from the approved inventory, or the plan contains an unapproved destructive action.

Routine automation must not run `terraform state mv`. Manual state operations are last-resort procedures requiring a separate reviewed runbook.

## Acceptance criteria

- ADR-0012 remains the complete accepted decision record;
- the plan and ADR statuses are consistent;
- all reusable modules have typed interfaces, tests, examples and compatible version ranges;
- executable examples remain pinned to tested tool versions;
- S3 policy tests verify TLS, SSE-header, SSE-mode and KMS-key enforcement;
- CI scans every Terraform file in the active root and rejects any `moved` block in B1;
- the active `dev` root and provider lock remain unchanged;
- all Python and Terraform checks pass on the final head;
- an independent review finds no unresolved P1 or P2 issue before merge.
