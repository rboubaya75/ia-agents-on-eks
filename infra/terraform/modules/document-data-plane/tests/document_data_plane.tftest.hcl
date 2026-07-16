mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }
}

variables {
  name_prefix                = "iaagents"
  environment                = "dev"
  aws_region                 = "eu-west-3"
  embedding_profile_alias    = "titan-v2"
  embedding_profile_revision = "rev-001"
  tags = {
    CostCenter = "platform"
  }
}

run "secure_aws_managed_defaults" {
  command = plan

  assert {
    condition     = aws_dynamodb_table.document_control.hash_key == "pk" && aws_dynamodb_table.document_control.range_key == "sk"
    error_message = "The document control table must use the application pk/sk schema."
  }

  assert {
    condition     = aws_dynamodb_table.document_control.billing_mode == "PAY_PER_REQUEST"
    error_message = "The initial data plane must use on-demand DynamoDB capacity."
  }

  assert {
    condition     = aws_dynamodb_table.document_control.deletion_protection_enabled
    error_message = "DynamoDB deletion protection must remain enabled."
  }

  assert {
    condition     = aws_s3_bucket.documents.force_destroy == false
    error_message = "The document bucket must not use force_destroy."
  }

  assert {
    condition = (
      aws_s3_bucket_public_access_block.documents.block_public_acls &&
      aws_s3_bucket_public_access_block.documents.block_public_policy &&
      aws_s3_bucket_public_access_block.documents.ignore_public_acls &&
      aws_s3_bucket_public_access_block.documents.restrict_public_buckets
    )
    error_message = "All S3 public-access-block settings must remain enabled."
  }

  assert {
    condition     = one(aws_s3_bucket_versioning.documents.versioning_configuration).status == "Enabled"
    error_message = "The document bucket must remain versioned."
  }

  assert {
    condition     = one(one(aws_s3_bucket_lifecycle_configuration.documents.rule).filter).prefix == "documents/uploads/"
    error_message = "The lifecycle rule must cover the complete temporary-upload prefix only."
  }

  assert {
    condition     = one(one(aws_s3_bucket_lifecycle_configuration.documents.rule).expiration).days == 1
    error_message = "Current temporary-upload versions must expire after exactly one day."
  }

  assert {
    condition     = one(one(aws_s3_bucket_lifecycle_configuration.documents.rule).noncurrent_version_expiration).noncurrent_days == 1
    error_message = "Noncurrent temporary-upload versions must expire after exactly one day."
  }

  assert {
    condition     = aws_sqs_queue.ingestion.fifo_queue && aws_sqs_queue.ingestion_dlq.fifo_queue
    error_message = "The ingestion queue and DLQ must both be FIFO."
  }

  assert {
    condition     = aws_s3vectors_vector_bucket.documents.force_destroy == false
    error_message = "The vector bucket must not use force_destroy."
  }

  assert {
    condition     = aws_s3vectors_index.documents["g001"].dimension == 1024 && aws_s3vectors_index.documents["g001"].distance_metric == "cosine"
    error_message = "The active vector index must match the configured immutable embedding contract."
  }

  assert {
    condition     = strcontains(aws_s3vectors_index.documents["g001"].index_name, "-g001-")
    error_message = "The active vector index name must expose the explicit generation."
  }

  assert {
    condition     = output.temporary_upload_prefix == "documents/uploads/"
    error_message = "The module output must match the application lifecycle-readiness prefix."
  }
}

run "customer_managed_kms_contract" {
  command = plan

  variables {
    encryption_mode = "aws:kms"
    create_kms_key  = true
  }

  assert {
    condition     = length(aws_kms_key.document) == 1
    error_message = "aws:kms with create_kms_key must create exactly one protected key."
  }

  assert {
    condition     = one(aws_s3_bucket_server_side_encryption_configuration.documents.rule).apply_server_side_encryption_by_default[0].sse_algorithm == "aws:kms"
    error_message = "The document bucket must use KMS encryption when requested."
  }
}

run "reject_missing_kms_contract" {
  command = plan

  variables {
    encryption_mode = "aws:kms"
    create_kms_key  = false
    kms_key_arn     = null
  }

  expect_failures = [aws_dynamodb_table.document_control]
}

run "reject_unsafe_worker_timing" {
  command = plan

  variables {
    queue_visibility_timeout_seconds = 600
    ingestion_lease_ttl_seconds      = 900
    heartbeat_interval_seconds       = 300
  }

  expect_failures = [aws_sqs_queue.ingestion]
}

run "reject_non_filterable_authorization_metadata" {
  command = plan

  variables {
    vector_non_filterable_metadata_keys = [
      "allowedRoles",
      "checksum",
    ]
  }

  expect_failures = [aws_s3vectors_index.documents["g001"]]
}

run "reject_index_prefix_inside_temporary_uploads" {
  command = plan

  variables {
    document_index_prefix = "documents/uploads"
  }

  expect_failures = [aws_s3_bucket_lifecycle_configuration.documents]
}

run "parallel_vector_index_migration" {
  command = plan

  variables {
    embedding_profile_revision = "rev-002"
    vector_index_generation    = "g002"
    retained_vector_index_contracts = {
      g001 = {
        embedding_profile_alias    = "titan-v2"
        embedding_profile_revision = "rev-001"
        embedding_dimensions       = 1024
        distance_metric            = "cosine"
        encryption_revision        = "enc-v1"
      }
    }
  }

  assert {
    condition     = length(aws_s3vectors_index.documents) == 2
    error_message = "Migration must retain the previous index while creating the new active index."
  }

  assert {
    condition     = contains(keys(aws_s3vectors_index.documents), "g001") && contains(keys(aws_s3vectors_index.documents), "g002")
    error_message = "Both the retained and active generations must exist in the same plan."
  }

  assert {
    condition     = aws_s3vectors_index.documents["g001"].index_name != aws_s3vectors_index.documents["g002"].index_name
    error_message = "Retained and active generations must have distinct immutable index names."
  }

  assert {
    condition     = output.vector_index_name == aws_s3vectors_index.documents["g002"].index_name
    error_message = "Application outputs must select only the active generation during migration."
  }
}
