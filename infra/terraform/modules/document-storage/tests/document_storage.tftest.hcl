mock_provider "aws" {}

override_resource {
  target          = aws_s3_bucket.this
  override_during = plan
  values = {
    id     = "iaagents-dev-123456789012-euwest3-docs"
    bucket = "iaagents-dev-123456789012-euwest3-docs"
    arn    = "arn:aws:s3:::iaagents-dev-123456789012-euwest3-docs"
  }
}

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
    condition     = one(aws_s3_bucket_ownership_controls.this.rule).object_ownership == "BucketOwnerEnforced"
    error_message = "The document bucket must enforce bucket-owner ownership."
  }

  assert {
    condition     = one(aws_s3_bucket_versioning.this.versioning_configuration).status == "Enabled"
    error_message = "The document bucket must remain versioned."
  }

  assert {
    condition     = one(one(aws_s3_bucket_server_side_encryption_configuration.this.rule).apply_server_side_encryption_by_default).sse_algorithm == "AES256"
    error_message = "AES256 must remain the default AWS-managed encryption mode."
  }

  assert {
    condition     = one(aws_s3_bucket_server_side_encryption_configuration.this.rule).bucket_key_enabled == false
    error_message = "S3 Bucket Keys must remain disabled for AES256."
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
    condition     = length(jsondecode(aws_s3_bucket_policy.this.policy).Statement) == 3
    error_message = "AES256 policy must contain exactly the TLS and SSE enforcement statements."
  }

  assert {
    condition = length([
      for statement in jsondecode(aws_s3_bucket_policy.this.policy).Statement : statement
      if try(statement.Sid, "") == "DenyInsecureTransport" &&
      try(statement.Effect, "") == "Deny" &&
      try(statement.Principal, "") == "*" &&
      try(statement.Action, "") == "s3:*" &&
      try(statement.Condition.Bool["aws:SecureTransport"], "") == "false" &&
      contains(try(statement.Resource, []), "arn:aws:s3:::iaagents-dev-123456789012-euwest3-docs") &&
      contains(try(statement.Resource, []), "arn:aws:s3:::iaagents-dev-123456789012-euwest3-docs/*")
    ]) == 1
    error_message = "The bucket policy must deny all non-TLS access to the bucket and its objects."
  }

  assert {
    condition = length([
      for statement in jsondecode(aws_s3_bucket_policy.this.policy).Statement : statement
      if try(statement.Sid, "") == "DenyMissingEncryptionHeader" &&
      try(statement.Effect, "") == "Deny" &&
      try(statement.Principal, "") == "*" &&
      try(statement.Action, "") == "s3:PutObject" &&
      try(statement.Condition.Null["s3:x-amz-server-side-encryption"], "") == "true"
    ]) == 1
    error_message = "The bucket policy must deny uploads without an explicit SSE header."
  }

  assert {
    condition = length([
      for statement in jsondecode(aws_s3_bucket_policy.this.policy).Statement : statement
      if try(statement.Sid, "") == "DenyIncorrectEncryptionMode" &&
      try(statement.Effect, "") == "Deny" &&
      try(statement.Action, "") == "s3:PutObject" &&
      try(statement.Condition.StringNotEquals["s3:x-amz-server-side-encryption"], "") == "AES256"
    ]) == 1
    error_message = "AES256 policy must deny any other SSE mode."
  }

  assert {
    condition = length([
      for statement in jsondecode(aws_s3_bucket_policy.this.policy).Statement : statement
      if try(statement.Sid, "") == "DenyIncorrectKmsKey"
    ]) == 0
    error_message = "AES256 mode must not add a KMS-key enforcement statement."
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
    condition     = one(one(aws_s3_bucket_server_side_encryption_configuration.this.rule).apply_server_side_encryption_by_default).sse_algorithm == "aws:kms"
    error_message = "The bucket must use KMS encryption when requested."
  }

  assert {
    condition     = one(aws_s3_bucket_server_side_encryption_configuration.this.rule).bucket_key_enabled
    error_message = "S3 Bucket Keys must be enabled with KMS encryption."
  }

  assert {
    condition     = one(one(aws_s3_bucket_server_side_encryption_configuration.this.rule).apply_server_side_encryption_by_default).kms_master_key_id == "arn:aws:kms:eu-west-3:123456789012:key/11111111-2222-3333-4444-555555555555"
    error_message = "The default encryption configuration must use the exact supplied KMS key ARN."
  }

  assert {
    condition     = length(jsondecode(aws_s3_bucket_policy.this.policy).Statement) == 4
    error_message = "KMS policy must contain the TLS, SSE and exact-key enforcement statements."
  }

  assert {
    condition = length([
      for statement in jsondecode(aws_s3_bucket_policy.this.policy).Statement : statement
      if try(statement.Sid, "") == "DenyInsecureTransport" &&
      try(statement.Condition.Bool["aws:SecureTransport"], "") == "false"
    ]) == 1
    error_message = "KMS mode must retain the TLS-only policy."
  }

  assert {
    condition = length([
      for statement in jsondecode(aws_s3_bucket_policy.this.policy).Statement : statement
      if try(statement.Sid, "") == "DenyMissingEncryptionHeader" &&
      try(statement.Condition.Null["s3:x-amz-server-side-encryption"], "") == "true"
    ]) == 1
    error_message = "KMS mode must retain the explicit SSE-header requirement."
  }

  assert {
    condition = length([
      for statement in jsondecode(aws_s3_bucket_policy.this.policy).Statement : statement
      if try(statement.Sid, "") == "DenyIncorrectEncryptionMode" &&
      try(statement.Effect, "") == "Deny" &&
      try(statement.Action, "") == "s3:PutObject" &&
      try(statement.Condition.StringNotEquals["s3:x-amz-server-side-encryption"], "") == "aws:kms"
    ]) == 1
    error_message = "KMS policy must deny any SSE mode other than aws:kms."
  }

  assert {
    condition = length([
      for statement in jsondecode(aws_s3_bucket_policy.this.policy).Statement : statement
      if try(statement.Sid, "") == "DenyIncorrectKmsKey" &&
      try(statement.Effect, "") == "Deny" &&
      try(statement.Action, "") == "s3:PutObject" &&
      try(statement.Condition.StringNotEquals["s3:x-amz-server-side-encryption-aws-kms-key-id"], "") == "arn:aws:kms:eu-west-3:123456789012:key/11111111-2222-3333-4444-555555555555"
    ]) == 1
    error_message = "The bucket policy must deny uploads encrypted with any other KMS key."
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
