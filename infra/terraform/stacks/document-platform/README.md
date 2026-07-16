# Document platform stack

Contract version: `1.0.0-precutover`

This stack is the composition boundary for document-platform capability modules.

## 4C-0B1 scope

The current preparatory implementation composes only:

- `document-encryption`;
- `document-storage`.

It establishes typed deployment context, deterministic Phase 4B-compatible names, mandatory tags and the stable encryption override contract.

The stack is not called by `infra/terraform/environments/dev` in 4C-0B1. The existing aggregate module remains the sole live owner. No backend, provider, live root or `moved` block is introduced here.

Coordination and messaging are added in 4C-0B2. Vector storage, compatibility outputs and the atomic live cutover are completed in 4C-0B3.
