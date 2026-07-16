# ADR-0012: Terraform industrialization model

- Status: Accepted
- Date: 2026-07-16
- Accepted after review of PR #11

## Context

Phase 4B delivered a valid Terraform document data plane, but one aggregate module owns DynamoDB, S3, SQS, KMS and S3 Vectors. Its development root mainly forwards variables and does not form a meaningful composition boundary. Extending that structure with workload identity, Kubernetes and observability would increase coupling, migration risk and blast radius.

The detailed state-discovery, address-migration and cutover procedure is maintained in `docs/architecture/phase-4c0-terraform-industrialization-plan.md`.

## Decision

Terraform uses three layers:

```text
infra/terraform/
├── modules/<capability>/
├── stacks/document-platform/
└── live/<environment>/<region>/document-platform/
```

Capability modules are organized around cohesive operational responsibilities:

- `document-encryption`;
- `document-storage`;
- `document-coordination`;
- `ingestion-messaging`;
- `document-vector-store`;
- `eks-workload-identity`;
- `document-observability`.

Modules must not configure providers, backends or environments. They receive typed context from callers and expose stable capability contracts rather than complete provider resource objects.

`stacks/document-platform` resolves deterministic names, mandatory tags, cross-capability wiring and compatibility outputs. It contains no provider, backend or environment-specific AWS identifier.

The Phase 4B compatibility KMS instance remains the singleton `module.encryption`, preserving the target address approved for the B3 state migration. Capability-specific encryption overrides use separate module instances.

Live roots contain only deployment context, provider configuration, externally supplied backend configuration and invocation of the stack. State boundaries follow deployable stacks and environments, not individual leaf modules.

Reusable modules include README, typed variables, outputs, version constraints, Terraform tests and a complete example. Reusable modules and stacks use compatible Terraform/provider ranges; executable examples and live roots pin tested versions and lock files.

Naming and mandatory tags are platform policy. Additional tags cannot redefine environment, managed-by, project, workload, component, owner, cost-centre or data-classification keys. Mandatory values win map composition.

The Phase 4B shared KMS key is preserved during 4C-0 as a migration compatibility contract. Splitting keys or re-encrypting durable data requires a separate ADR and migration approval.

Validation is layered:

1. formatting, initialization and validation;
2. module and stack tests;
3. complete examples;
4. plan JSON policies for effective actions and values;
5. HCL-aware policies for lifecycle and source constraints;
6. controlled state-migration fixtures and approved state evidence.

## Migration sequence

- **4C-0B1:** add and test encryption and storage modules; legacy module remains sole live owner.
- **4C-0B2:** add and test coordination and messaging modules; legacy module remains sole live owner.
- **4C-0B3:** complete vector storage and the stack, introduce the live root and perform one atomic cutover with explicit `moved` blocks.
- **4C-0C:** add industrialized plan, HCL, migration-fixture and documentation-drift policies.

B1 and B2 must not change or partially apply live ownership. Before B3, an authorized operator must classify the target state, capture the required non-sensitive inventory and protected backup, and verify the provider lock.

Every changed address uses a static `moved` block. Each existing `for_each` key receives its own concrete move. A plan containing an unapproved delete, replacement, duplicate or missing address fails the phase gate.

## Alternatives rejected

- continue extending the aggregate module;
- create one module per AWS resource;
- apply each extraction independently;
- split state for every module immediately;
- publish modules before the state-preserving refactor;
- rely only on source-string checks or plan JSON;
- rebuild durable resources under new addresses.

## Consequences

Phase 4C feature implementation remains paused until 4C-0 is complete. Capability, stack and live-root boundaries become explicit and reusable. CI becomes broader. B1 and B2 are mergeable but not deployable as partial migrations. B3 requires current state evidence, exact moves and zero unapproved destruction or replacement.
