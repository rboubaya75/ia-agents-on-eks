# Document encryption module

Contract version: `1.0.0-precutover`

This capability module owns an optional customer-managed KMS key and alias. It also normalizes AWS-managed and existing-key configurations into one stable encryption contract.

## Ownership boundary

The module owns only:

- an optional `aws_kms_key`;
- an optional `aws_kms_alias`;
- rotation and deletion-protection settings;
- the effective non-secret encryption contract.

It does not own service-specific grants, workload IAM, provider configuration, backend configuration, account discovery or environment naming.

## Phase 4C-0 migration behavior

The module is preparatory in 4C-0B1. It is not called by the active development root and owns no deployed resource until the atomic 4C-0B3 cutover.

When Phase 4B uses a shared customer-managed key, B3 supplies the exact existing alias and address migration. Capability-specific keys remain possible through the stack interface but require a separate approved migration.

## Destruction behavior

A module-managed KMS key uses `prevent_destroy` and key rotation. Removing or replacing a key is never an incidental refactor operation.

## Validation

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
terraform test
```

The complete example is under `examples/complete`.
