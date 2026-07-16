# Document data-plane Terraform module

This module provisions only the durable AWS resources required by the merged document API and ingestion worker:

- one DynamoDB control table using the application `pk` and `sk` key schema;
- one private, versioned and encrypted S3 bucket;
- the exact one-day lifecycle required for current and noncurrent versions under `<document_source_prefix>/uploads/`;
- one encrypted FIFO ingestion queue and one encrypted FIFO DLQ;
- one protected S3 Vectors bucket with one active and zero or more retained immutable index generations;
- an optional customer-managed KMS key.

It does not create EKS, networking, Cognito, workload IAM roles, Pod Identity associations, Helm resources or GitHub deployment roles.

## Destruction boundary

The document bucket, DynamoDB table, module-managed KMS key, vector bucket and every vector index use `prevent_destroy`. Retiring one of these resources is a separate reviewed operation.

The chunk/index prefix must not equal or descend from the temporary-upload prefix. This prevents the one-day upload lifecycle from selecting durable chunks or vector manifests.

## Vector-index migration

A new embedding contract must not replace the active index in place. Before changing `vector_index_generation`, copy the previous active contract into `retained_vector_index_contracts`.

Example:

```hcl
vector_index_generation    = "g002"
embedding_profile_revision = "rev-002"

retained_vector_index_contracts = {
  g001 = {
    embedding_profile_alias    = "titan-v2"
    embedding_profile_revision = "rev-001"
    embedding_dimensions       = 1024
    distance_metric            = "cosine"
    encryption_revision        = "enc-v1"
  }
}
```

Terraform then manages `g001` and `g002` concurrently while application outputs select only `g002`. Remove `g001` only in a later retirement pull request after re-indexing, cutover and stabilization. Because every index uses `prevent_destroy`, that retirement requires an explicit reviewed lifecycle change.

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
