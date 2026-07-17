# Document platform stack

Contract version: `1.0.0-precutover`

This stack is the composition boundary for document-platform capability modules.

## 4C-0B1 scope

The current preparatory implementation composes only:

- `document-encryption`;
- `document-storage`.

The Phase 4B compatibility encryption module is a singleton named `module.encryption`. This preserves the approved B3 migration address. Capability-specific overrides are isolated under `module.capability_encryption` and do not change the compatibility address. Omitted or explicitly null override entries resolve to the compatibility default and create no additional module instance.

## Interface contract

Inputs are grouped into typed objects:

- `context` supplies deterministic naming values and mandatory organizational tags;
- `encryption` supplies one compatibility default and optional per-capability overrides;
- `storage` supplies document prefixes and lifecycle parameters.

The current outputs expose Phase 4B-compatible bucket identifiers, prefixes, lifecycle identifiers and the effective document-storage encryption contract. `document_kms_key_arn` always exposes the key actually used by document storage, including when storage overrides the compatibility default. `document_kms_alias_name` remains a migration diagnostic for a module-managed compatibility key.

## Ownership and migration boundary

The stack is not called by `infra/terraform/environments/dev` in 4C-0B1. The existing aggregate module remains the sole live owner. No backend, provider, live root or `moved` block is introduced here.

Coordination and messaging are added in 4C-0B2. Vector storage, remaining compatibility outputs and the atomic live cutover are completed in 4C-0B3.
