# ADR-0012: Terraform industrialization model

- Status: Proposed
- Date: 2026-07-16

## Context

Phase 4B delivered a valid Terraform implementation for the document data plane, but the repository structure is not yet suitable for long-term platform engineering at enterprise scale.

The current `document-data-plane` module owns DynamoDB, S3, SQS, KMS and S3 Vectors in one module. Those capabilities have different operational lifecycles, security boundaries, migration patterns and failure domains. The development root module mainly forwards variables to that module and therefore does not provide a meaningful composition boundary.

The current implementation also mixes concerns that should be separated:

- resource provisioning;
- environment and account discovery;
- naming and tagging conventions;
- application runtime configuration;
- vector-index migration state;
- infrastructure policy validation.

Some CI controls inspect Terraform source with exact string searches. Those checks are useful as temporary regression guards, but they are not a durable policy-as-code mechanism because equivalent Terraform can be formatted or expressed differently.

Phase 4C must add workload identity, Helm deployment, observability and deployment automation. Extending the current module model would increase coupling and make later environments harder to operate safely. The module and stack model must therefore be corrected before Phase 4C implementation continues.

## Decision

### Architectural layers

Terraform will use three explicit layers.

```text
infra/terraform/
├── modules/
│   ├── document-storage/
│   ├── document-coordination/
│   ├── ingestion-messaging/
│   ├── document-vector-store/
│   ├── eks-workload-identity/
│   └── document-observability/
├── stacks/
│   └── document-platform/
└── live/
    └── <environment>/
        └── <region>/
            └── document-platform/
```

The layers have different responsibilities.

#### Reusable capability modules

A capability module owns one cohesive operational capability and its directly coupled resources.

- `document-storage` owns the document S3 bucket, versioning, encryption contract and lifecycle rules.
- `document-coordination` owns the DynamoDB control table and its durability controls.
- `ingestion-messaging` owns the FIFO ingestion queue, FIFO dead-letter queue and redrive controls.
- `document-vector-store` owns the S3 Vectors bucket and immutable index generations.
- `eks-workload-identity` owns separate API and worker roles, policies and Pod Identity associations.
- `document-observability` owns document-specific log groups, metric filters and alarms.

A capability module must not configure providers, remote state, deployment environments or unrelated application settings. It must not discover organization-specific context merely to build names or tags. Required context is provided by the caller through typed inputs.

Modules remain intentionally coarse enough to preserve invariants. The design does not create one module per AWS resource.

#### Stack composition

`stacks/document-platform` composes the capability modules and wires their outputs to dependent inputs. It is the authoritative location for cross-capability contracts such as:

- storage prefixes consumed by IAM;
- queue identifiers consumed by IAM and observability;
- vector-store identifiers consumed by IAM and runtime configuration;
- shared encryption decisions;
- application runtime outputs;
- feature-level validation across multiple modules.

The stack contains no backend configuration and no environment-specific AWS identifiers.

#### Live environment roots

Each `live/<environment>/<region>/document-platform` root owns only deployment composition:

- backend configuration supplied externally;
- provider configuration;
- environment and region selection;
- external platform prerequisites;
- approved environment data;
- invocation of the versioned stack contract;
- environment outputs.

Live roots must remain small. They must not reimplement resources or duplicate module logic.

### State and blast-radius boundaries

Remote state is separated by deployable stack and environment. The document platform has its own state and is not combined with networking, EKS cluster provisioning, identity foundations or unrelated applications.

Splitting capability modules does not automatically require separate state for every module. The first target keeps document capabilities composed in one document-platform state because their initial deployment and application contract are coordinated. A separate state is introduced only when ownership, lifecycle or failure isolation justifies it.

Any later state split requires its own ADR and a reviewed migration plan.

### Module interface standards

Every reusable module must include:

```text
<module>/
├── README.md
├── main.tf or capability-specific resource files
├── variables.tf
├── outputs.tf
├── versions.tf
├── tests/
│   └── *.tftest.hcl
└── examples/
    └── complete/
```

Module interfaces follow these rules:

- strict Terraform types and validations;
- explicit descriptions for every variable and output;
- no provider blocks in reusable modules;
- no backend blocks in modules or stacks;
- no hardcoded account IDs, ARNs, regions, project names or environment names;
- no static credentials or secret values;
- nullable inputs used only when absence is a supported state;
- sensitive outputs marked as sensitive;
- deterministic names supplied by the stack rather than independently invented by each module;
- outputs expose stable capability contracts rather than entire AWS resource objects;
- breaking interface changes require a major contract revision.

Large flat parameter lists are avoided at the live layer. The stack accepts typed configuration objects grouped by capability. Leaf modules keep explicit inputs where that improves clarity and policy testing.

### Naming, tags and organizational context

Naming and mandatory tags are platform policy, not leaf-module policy.

The live root supplies a typed deployment context to the stack. The stack resolves deterministic resource names and mandatory tags, then passes final values to capability modules.

The deployment context includes only non-secret values such as:

- workload identifier;
- environment;
- region;
- owner or cost-centre metadata supplied by the environment;
- data classification;
- managed-by marker.

Leaf modules do not call `aws_caller_identity` solely to construct names. Account and partition data required for policy construction are resolved at the root or stack boundary and passed explicitly.

### Application configuration boundary

Infrastructure inputs and application runtime inputs are separate contracts.

Terraform owns identifiers and operational settings produced by infrastructure, including bucket names, queue URLs, table names, vector index names and workload role ARNs.

Application-owned settings such as feature activation, embedding model selection, pipeline revision and worker tuning are not embedded into unrelated infrastructure modules. The stack may validate cross-system invariants and emit a typed runtime-settings output, but deployment configuration remains the consumer boundary.

### Module distribution and versioning

During this refactor, capability modules remain in the repository and are consumed with local paths to permit atomic state-preserving migration.

The repository records module contract versions in module documentation and release notes. Once a module is reused by another repository or independently owned team, it must be promoted to the approved private Terraform module registry and consumed with a pinned semantic version.

Floating Git references, unpinned registry versions and direct consumption of a mutable default branch are forbidden.

### Validation and policy as code

Validation is layered.

1. `terraform fmt`, `terraform init -backend=false` and `terraform validate` validate syntax and provider contracts.
2. `terraform test` validates module behavior, failure cases and plan-time invariants.
3. Complete examples prove that each module can be consumed independently.
4. Stack tests validate wiring and cross-capability contracts.
5. A generated Terraform plan JSON is evaluated by policy as code for organization-wide controls.
6. Static source searches are retained only for narrow temporary regression cases and must not be the primary policy mechanism.

Policy checks must cover at least:

- no public S3 access;
- required encryption and TLS controls;
- no wildcard IAM actions or resources without an approved exception;
- deletion protection and `prevent_destroy` on durable resources;
- mandatory tags;
- no long-lived credentials;
- exact GitHub OIDC trust when deployment roles are added;
- immutable image references when Helm deployment is added.

Tool and GitHub Action versions are pinned. Dependency updates are automated through reviewed pull requests.

### Documentation and ownership

Generated module input/output documentation is checked for drift in CI. Each capability has an explicit ownership entry in `CODEOWNERS` when the repository has multiple maintainers.

Architecture decisions, migration procedures and operational runbooks are kept under `docs/`. Examples are executable configurations, not copy-paste fragments that bypass module contracts.

### Migration from Phase 4B

The refactor must preserve deployed resource identity and must not silently destroy or recreate durable resources.

The migration sequence is:

1. freeze new Phase 4C resource implementation;
2. capture the current Phase 4B resource-address inventory;
3. introduce the new capability modules and stack composition without applying;
4. add explicit Terraform `moved` blocks for every resource address that changes;
5. prove through tests and a saved development plan that no durable resource is destroyed or replaced;
6. retain compatibility outputs required by the application and later phases;
7. merge and apply the structural migration only after independent review;
8. remove transitional compatibility code in a later reviewed change.

A migration plan containing any unapproved destroy or replacement action fails the phase gate.

No `terraform state mv` command is placed in routine CI. Manual state operations are a last-resort, separately reviewed runbook when declarative `moved` blocks cannot express the migration.

### Delivery sequence

The implementation is divided into independently reviewable sub-lots.

- **4C-0A — Architecture:** this ADR and the migration plan.
- **4C-0B — Structural refactor:** capability modules, stack and live root with state-preserving moves.
- **4C-0C — Industrialized validation:** module examples, stack tests, plan JSON policy checks and documentation drift checks.
- **4C-1 — Workload identity:** API and worker IAM plus EKS Pod Identity.
- **4C-2 — Helm workloads and runtime configuration.**
- **4C-3 — Observability and deployment workflows.**
- **4C-4 — Development deployment and positive/negative AWS integration tests.**

No sub-lot starts until the preceding sub-lot is reviewed and approved.

## Alternatives considered

### Continue extending `document-data-plane`

Rejected. It would combine storage, messaging, coordination, vectors, identity and observability in one lifecycle and increase the impact of every change.

### Create one module per AWS resource

Rejected. That structure creates excessive wiring, weakens capability invariants and shifts complexity into every caller.

### Separate Terraform state for every capability immediately

Rejected. Module boundaries and state boundaries solve different problems. Premature state fragmentation increases deployment coordination and remote-state coupling.

### Publish all modules to a registry before refactoring

Rejected. Registry publication before the state-preserving refactor would make an atomic migration harder. Registry promotion is triggered by proven cross-repository reuse or independent ownership.

### Keep source-code `grep` checks as the main security gate

Rejected. String checks do not reliably evaluate the effective Terraform plan and are too sensitive to equivalent syntax changes.

### Rebuild resources under the new module addresses

Rejected. Durable documents, coordination records and vector indexes must retain identity. The refactor is structural, not a redeployment.

## Consequences

- Phase 4C implementation is paused until 4C-0 is approved and completed.
- Terraform code has explicit capability, stack and environment boundaries.
- Module reuse becomes possible without embedding project-specific discovery or policy.
- State remains aligned with deployable blast radius rather than file layout.
- CI becomes more expensive because it validates examples, stack composition and plan policies.
- The initial refactor requires temporary compatibility outputs and `moved` blocks.
- A zero-destroy migration plan becomes a mandatory acceptance criterion.
- Workload identity, Helm and observability are implemented only after the Terraform foundation is corrected.

## Required follow-up

1. review and approve this ADR;
2. approve the detailed 4C-0 migration plan;
3. implement 4C-0B without adding new AWS capabilities;
4. demonstrate zero unapproved destroy or replacement actions;
5. implement 4C-0C validation and policy controls;
6. resume 4C-1 workload identity only after 4C-0 is merged.