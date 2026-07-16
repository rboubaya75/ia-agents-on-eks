# Phase 4C-0B1 — Preparatory encryption and storage modules

## Scope

This sub-lot extracts reusable `document-encryption` and `document-storage` capability modules and introduces the initial `document-platform` stack contract.

## Ownership gate

`infra/terraform/environments/dev` remains unchanged and continues to call only `module.document_data_plane`.

The new modules and stack are validated through Terraform tests and isolated complete examples. They own no live AWS resource in this sub-lot. No backend, provider root, state address or `moved` block is added.

## Review remediations

- ADR-0012 is accepted before implementation proceeds;
- the Phase 4B compatibility encryption module remains the singleton `module.encryption`, preserving the approved B3 migration address;
- capability-specific encryption overrides use the separate `module.capability_encryption` collection;
- mandatory platform tag keys cannot be redefined through `additional_tags`;
- reusable modules and the stack declare compatible Terraform and AWS provider ranges, while executable examples remain pinned;
- tests verify the exact Phase 4B KMS alias and reject reserved tag overrides.

## Compatibility requirements

- document bucket naming remains compatible with Phase 4B;
- the shared KMS alias remains compatible with Phase 4B when a key is created;
- storage prefixes and lifecycle identifiers remain compatible;
- bucket public-access, versioning, encryption, lifecycle and destruction controls are preserved;
- the stack exposes a stable per-capability encryption override contract without splitting any live key.

## Acceptance criteria

- both modules contain strict inputs, stable outputs, README, tests and a complete example;
- the stack has no provider or backend block;
- the active development root contains no reference to the new modules or stack;
- Terraform formatting, initialization, validation and tests pass;
- existing Python quality gates remain green;
- no coordination, messaging, vector, IAM, Kubernetes or observability resource is introduced.
