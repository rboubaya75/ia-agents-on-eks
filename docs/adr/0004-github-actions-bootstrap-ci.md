# ADR-0004: GitHub Actions for bootstrap CI

- Status: Accepted temporarily
- Date: 2026-07-11

## Context

The target architecture originally named GitLab CI, but the repository is currently hosted and developed on GitHub and GitLab is not yet in scope.

## Decision

Use GitHub Actions for Phase 1 quality checks. No static AWS access keys will be introduced. When AWS deployment begins, GitHub OIDC federation will be required.

## Consequences

- The CI implementation changes, but the no-static-credentials security invariant remains.
- A later migration to GitLab requires a new ADR and equivalent quality gates.
