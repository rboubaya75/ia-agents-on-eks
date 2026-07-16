# Document data-plane Terraform module

This module provisions only the durable AWS resources required by the merged document API and ingestion worker:

- one DynamoDB control table using the application `pk` and `sk` key schema;
- one private, versioned and encrypted S3 bucket;
- the exact one-day lifecycle rule required for `<document_source_prefix>/uploads/`;
- one encrypted FIFO ingestion queue and one encrypted FIFO DLQ;
- one protected S3 Vectors bucket and one immutable index generation;
- an optional customer-managed KMS key.

It does not create EKS, networking, Cognito, workload IAM roles, Pod Identity associations, Helm resources or GitHub deployment roles.

## Destruction boundary

The document bucket, DynamoDB table, module-managed KMS key, vector bucket and vector index use `prevent_destroy`. Retiring one of these resources is a separate reviewed operation. A vector contract change must create a new `vector_index_generation`; it must not replace the active index in place.

## Application contract

The `application_runtime_settings` output provides the authoritative non-secret settings required by the backend. `IA_DOCUMENT_API_ENABLED`, `IA_EMBEDDING_MODEL_ID` and `IA_DOCUMENT_PIPELINE_VERSION` remain deployment inputs because this module does not own feature activation or model resolution.

## Encryption

`encryption_mode = "AES256"` uses AWS-managed encryption and accepts no KMS key input.

`encryption_mode = "aws:kms"` requires exactly one of:

- `create_kms_key = true`; or
- `kms_key_arn = <existing key ARN>`.

Phase 4C will grant the API and worker narrowly scoped use of the resulting resources and key.

## Validation

Run from this directory:

```bash
terraform fmt -check -recursive
terraform init -backend=false
terraform validate
terraform test
```

Terraform is pinned to `1.15.8` and the AWS provider to `6.55.0`.
