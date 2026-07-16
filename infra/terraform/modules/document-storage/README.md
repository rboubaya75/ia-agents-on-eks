# Document storage module

Contract version: `1.0.0-precutover`

This capability module owns the private S3 bucket and controls used by the document API and ingestion pipeline.

## Ownership boundary

The module owns:

- the document bucket;
- complete public-access blocking;
- bucket-owner-enforced ownership;
- versioning;
- default encryption;
- the one-day temporary-upload lifecycle;
- TLS and encryption-enforcement bucket policy.

The caller supplies final names, prefixes, tags and an explicit encryption contract. The module performs no account discovery and configures neither providers nor remote state.

## Interface contract

Inputs:

- `bucket_name`: final globally unique bucket name;
- `source_prefix` and `index_prefix`: application storage roots;
- `temporary_upload_expiration_days`: fixed one-day readiness retention;
- `abort_incomplete_multipart_upload_days`: bounded multipart cleanup interval;
- `lifecycle_rule_id`: stable migration-compatible rule identifier;
- `encryption`: explicit `{ mode, kms_key_arn }` contract;
- `tags`: final tags supplied by the stack.

Outputs:

- bucket name and ARN;
- source, index, temporary-upload, immutable-source and chunk prefixes;
- lifecycle rule identifier;
- effective storage encryption contract.

The module rejects KMS mode without a valid customer key and rejects an index prefix that equals or descends from the temporary-upload prefix.

## Phase 4C-0 migration behavior

The module is preparatory in 4C-0B1. It is validated through tests and examples but is not called by the active development root. The Phase 4B aggregate module remains the sole live owner until the atomic 4C-0B3 cutover.

## Destruction behavior

The bucket uses `force_destroy = false` and `prevent_destroy`. Structural refactoring must preserve its identity.

## Complete example and validation

The executable example is under `examples/complete` and pins tested Terraform and AWS provider versions.

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
terraform test
```
