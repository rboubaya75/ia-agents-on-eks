resource "aws_s3_bucket" "documents" {
  bucket        = local.document_bucket_name
  force_destroy = false

  tags = merge(local.common_tags, {
    Name = local.document_bucket_name
  })

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = length(local.document_bucket_name) <= 63
      error_message = "The derived document bucket name exceeds 63 characters; shorten name_prefix or environment."
    }
  }
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket = aws_s3_bucket.documents.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    bucket_key_enabled = var.encryption_mode == "aws:kms"

    apply_server_side_encryption_by_default {
      sse_algorithm     = var.encryption_mode
      kms_master_key_id = var.encryption_mode == "aws:kms" ? local.effective_kms_key_arn : null
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    id     = local.lifecycle_rule_id
    status = "Enabled"

    filter {
      prefix = local.temporary_upload_prefix
    }

    expiration {
      days = var.temporary_upload_expiration_days
    }

    noncurrent_version_expiration {
      noncurrent_days = var.temporary_upload_expiration_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = var.abort_incomplete_multipart_upload_days
    }
  }

  rule {
    id     = local.lifecycle_marker_rule_id
    status = "Enabled"

    filter {
      prefix = local.temporary_upload_prefix
    }

    expiration {
      expired_object_delete_marker = true
    }
  }

  depends_on = [aws_s3_bucket_versioning.documents]

  lifecycle {
    precondition {
      condition     = !startswith("${var.document_index_prefix}/", local.temporary_upload_prefix)
      error_message = "document_index_prefix must not be equal to or nested under the temporary upload prefix."
    }
  }
}

resource "aws_s3_bucket_policy" "documents" {
  bucket = aws_s3_bucket.documents.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid       = "DenyInsecureTransport"
          Effect    = "Deny"
          Principal = "*"
          Action    = "s3:*"
          Resource = [
            aws_s3_bucket.documents.arn,
            "${aws_s3_bucket.documents.arn}/*",
          ]
          Condition = {
            Bool = {
              "aws:SecureTransport" = "false"
            }
          }
        },
        {
          Sid       = "DenyMissingEncryptionHeader"
          Effect    = "Deny"
          Principal = "*"
          Action    = "s3:PutObject"
          Resource  = "${aws_s3_bucket.documents.arn}/*"
          Condition = {
            Null = {
              "s3:x-amz-server-side-encryption" = "true"
            }
          }
        },
        {
          Sid       = "DenyIncorrectEncryptionMode"
          Effect    = "Deny"
          Principal = "*"
          Action    = "s3:PutObject"
          Resource  = "${aws_s3_bucket.documents.arn}/*"
          Condition = {
            StringNotEquals = {
              "s3:x-amz-server-side-encryption" = var.encryption_mode
            }
          }
        },
      ],
      var.encryption_mode == "aws:kms" ? [
        {
          Sid       = "DenyIncorrectKmsKey"
          Effect    = "Deny"
          Principal = "*"
          Action    = "s3:PutObject"
          Resource  = "${aws_s3_bucket.documents.arn}/*"
          Condition = {
            StringNotEquals = {
              "s3:x-amz-server-side-encryption-aws-kms-key-id" = local.effective_kms_key_arn
            }
          }
        },
      ] : [],
    )
  })

  depends_on = [aws_s3_bucket_public_access_block.documents]
}
