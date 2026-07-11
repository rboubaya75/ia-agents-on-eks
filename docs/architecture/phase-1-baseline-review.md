# Phase 1 repository baseline review

## Finding

The repository baseline contains inherited AWS workshop files at the root and under `home/` and `modules/`. They are unrelated to the target platform architecture. The inherited `backend_override.tf` also contains a hardcoded Terraform state bucket and region, which violates the target project's configuration rules.

## Phase 1 treatment

These files are not modified or silently deleted in Phase 1. Ruff, mypy and pytest are scoped to the new application packages and tests. This scope is explicit rather than an assertion that the whole inherited repository is compliant.

## Required decision before infrastructure work

Choose one of the following before Phase 6:

1. remove the inherited workshop content after confirming it is no longer needed;
2. move it into a clearly isolated archival repository;
3. reset the target repository to the clean platform baseline.

No Terraform from the inherited workshop content may be deployed as part of this platform.
