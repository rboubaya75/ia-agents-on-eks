# Document data-plane Terraform module

This module provisions only the durable AWS resources required by the merged document API and ingestion worker:

- one DynamoDB control table using the application `pk` and `sk` key schema;
- one private, versioned and encrypted S3 bucket;
- complete temporary-upload lifecycle controls for `<document_source_prefix>/uploads/`;
- one encrypted FIFO ingestion queue and one encrypted FIFO DLQ;
- one protected S3 Vectors bucket and one or more retained immutable index generations;
- an optional customer-managed KMS key.

It does not create EKS, networking, Cognito, workload IAM roles, Pod Identity associations, Helm resources or GitHub deployment roles.

## Temporary-upload lifecycle

The versioned document bucket removes temporary content through three coordinated controls scoped only to the complete upload prefix:

- current versions expire after exactly one day;
- noncurrent versions expire after exactly one day;
- expired delete markers are removed;
- incomplete multipart uploads are aborted independently.

The module rejects a `document_index_prefix` equal to or below the temporary-upload prefix, preventing chunks and vector manifests from inheriting the temporary lifecycle.

## Vector index generations

`vector_index_generations` is a map keyed by immutable generation identifiers such as `g001` and `g002`. Exactly one generation must set `active = true`.

A migration declares the current generation as retained and inactive while adding the next active generation. Terraform therefore manages both index resources concurrently. Application outputs select only the active index; the `vector_indexes` output exposes all retained generations for migration, verification and later retirement.

Every vector index uses `prevent_destroy`. Removing a retained generation is a separate reviewed retirement operation and cannot happen implicitly during a model, dimension, metric or metadata-contract change.

## Destruction boundary

The document bucket, DynamoDB table, module-managed KMS key, vector bucket and every vector index use `prevent_destroy`. Retiring one of these resources is a separate reviewed operation.

## Application contract

The `application_runtime_settings` output provides the authoritative non-secret settings required by the backend and selects only the active vector index generation. `IA_DOCUMENT_API_ENABLED`, `IA_EMBEDDING_MODEL_ID` and `IA_DOCUMENT_PIPELINE_VERSION` remain deployment inputs because this module does not own feature activation or model resolution.

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
