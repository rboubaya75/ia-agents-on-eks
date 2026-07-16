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

## Interface contract

Inputs:

- `mode`: `AES256` or `aws:kms`;
- `create_customer_key`: whether the module creates the customer key;
- `existing_key_arn`: optional existing key, mutually exclusive with key creation;
- `key_alias_name` and `key_description`: final metadata supplied by the stack;
- `deletion_window_days` and `multi_region`: key lifecycle properties;
- `tags`: final tags supplied by the stack.

Outputs:

- `effective_key_arn`: the effective customer-key ARN, or null for `AES256`;
- `managed_key_arn`, `managed_alias_arn` and `managed_alias_name`: identifiers created by this module;
- `contract`: the stable `{ mode, kms_key_arn }` object consumed by capability modules.

Invalid combinations fail during planning: `AES256` cannot configure a customer key, while `aws:kms` requires exactly one of key creation or an existing key ARN.

## Phase 4C-0 migration behavior

The module is preparatory in 4C-0B1. It is not called by the active development root and owns no deployed resource until the atomic 4C-0B3 cutover.

When Phase 4B uses a shared customer-managed key, B3 supplies the exact existing alias and address migration. The compatibility instance remains a singleton so its Terraform address matches the approved migration matrix. Capability-specific keys use separate stack module instances and require a separately approved migration.

## Destruction behavior

A module-managed KMS key uses `prevent_destroy` and key rotation. Removing or replacing a key is never an incidental refactor operation.

## Complete example and validation

The executable example is under `examples/complete` and pins tested Terraform and AWS provider versions.

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
terraform test
```
