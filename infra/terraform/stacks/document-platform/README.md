# Document platform stack

Contract version: `1.0.0-precutover`

This stack is the composition boundary for document-platform capability modules.

## 4C-0B1 scope

The current preparatory implementation composes only:

- `document-encryption`;
- `document-storage`.

The Phase 4B compatibility encryption module is a singleton named `module.encryption`. This preserves the approved B3 migration address. Capability-specific overrides are isolated under `module.capability_encryption` and do not change the compatibility address.

The stack establishes typed deployment context, deterministic Phase 4B-compatible names, protected mandatory tags and stable encryption contracts.

The stack is not called by `infra/terraform/environments/dev` in 4C-0B1. The existing aggregate module remains the sole live owner. No backend, provider, live root or `moved` block is introduced here.

Coordination and messaging are added in 4C-0B2. Vector storage, compatibility outputs and the atomic live cutover are completed in 4C-0B3.
