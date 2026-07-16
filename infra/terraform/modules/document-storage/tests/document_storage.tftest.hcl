mock_provider "aws" {}

variables {
  bucket_name       = "iaagents-dev-123456789012-euwest3-docs"
  lifecycle_rule_id = "iaagents-dev-temporary-uploads"
  encryption = {
    mode = "AES256"
  }
  tags = {
    Environment = "dev"
    ManagedBy   = "Terraform"
  }
}

run "secure_aws_managed_defaults" {
  command = plan

  assert {
    condition     = aws_s3_bucket.this.force_destroy == false
    error_message = "The document bucket must not use force_destroy."
  }

  assert {
    condition = (
      aws_s3_bucket_public_access_block.this.block_public_acls &&
      aws_s3_bucket_public_access_block.this.block_public_policy &&
      aws_s3_bucket_public_access_block.this.ignore_public_acls &&
      aws_s3_bucket_public_access_block.this.restrict_public_buckets
    )
    error_message = "All S3 public-access-block settings must remain enabled."
  }

  assert {
    condition     = one(aws_s3_bucket_versioning.this.versioning_configuration).status == "Enabled"
    error_message = "The document bucket must remain versioned."
  }

  assert {
    condition     = one(one(aws_s3_bucket_lifecycle_configuration.this.rule).filter).prefix == "documents/uploads/"
    error_message = "The lifecycle rule must cover the exact temporary-upload prefix."
  }

  assert {
    condition     = one(one(aws_s3_bucket_lifecycle_configuration.this.rule).expiration).days == 1
    error_message = "Current temporary-upload versions must expire after one day."
  }

  assert {
    condition     = one(one(aws_s3_bucket_lifecycle_configuration.this.rule).noncurrent_version_expiration).noncurrent_days == 1
    error_message = "Noncurrent temporary-upload versions must expire after one day."
  }

  assert {
    condition     = output.temporary_upload_prefix == "documents/uploads/"
    error_message = "The output must preserve the document API readiness prefix."
  }

  assert {
    condition = contains(
      [for statement in jsondecode(aws_s3_bucket_policy.this.policy).Statement : statement.Sid],
      "DenyInsecureTransport",
    )
    error_message = "The bucket policy must deny non-TLS transport."
  }

  assert {
    condition = contains(
      [for statement in jsondecode(aws_s3_bucket_policy.this.policy).Statement : statement.Sid],
      "DenyMissingEncryptionHeader",
    )
    error_message = "The bucket policy must deny uploads without an SSE header."
  }

  assert {
    condition = contains(
      [for statement in jsondecode(aws_s3_bucket_policy.this.policy).Statement : statement.Sid],
      "DenyIncorrectEncryptionMode",
    )
    error_message = "The bucket policy must deny an incorrect encryption mode."
  }

  assert {
    condition = length([
      for statement in jsondecode(aws_s3_bucket_policy.this.policy).Statement : statement
      if statement.Sid == "DenyIncorrectKmsKey"
    ]) == 0
    error_message = "AES256 mode must not create a KMS-key enforcement statement."
  }
}

run "customer_managed_encryption" {
  command = plan

  variables {
    encryption = {
      mode        = "aws:kms"
      kms_key_arn = "arn:aws:kms:eu-west-3:123456789012:key/11111111-2222-3333-4444-555555555555"
    }
  }

  assert {
    condition     = one(aws_s3_bucket_server_side_encryption_configuration.this.rule).apply_server_side_encryption_by_default[0].sse_algorithm == "aws:kms"
    error_message = "The bucket must use KMS encryption when requested."
  }

  assert {
    condition     = one(aws_s3_bucket_server_side_encryption_configuration.this.rule).bucket_key_enabled
    error_message = "S3 Bucket Keys must be enabled with KMS encryption."
  }

  assert {
    condition = contains(
      [for statement in jsondecode(aws_s3_bucket_policy.this.policy).Statement : statement.Sid],
      "DenyIncorrectKmsKey",
    )
    error_message = "KMS mode must enforce the expected customer-managed key."
  }

  assert {
    condition = one([
      for statement in jsondecode(aws_s3_bucket_policy.this.policy).Statement :
      statement.Condition.StringNotEquals["s3:x-amz-server-side-encryption-aws-kms-key-id"]
      if statement.Sid == "DenyIncorrectKmsKey"
    ]) == "arn:aws:kms:eu-west-3:123456789012:key/11111111-2222-3333-4444-555555555555"
    error_message = "The bucket policy must enforce the exact configured KMS key ARN."
  }
}

run "reject_missing_kms_key" {
  command = plan

  variables {
    encryption = {
      mode = "aws:kms"
    }
  }

  expect_failures = [var.encryption]
}

run "reject_index_prefix_inside_temporary_uploads" {
  command = plan

  variables {
    index_prefix = "documents/uploads"
  }

  expect_failures = [aws_s3_bucket_lifecycle_configuration.this]
}
