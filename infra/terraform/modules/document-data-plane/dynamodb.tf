resource "aws_dynamodb_table" "document_control" {
  name         = local.control_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  deletion_protection_enabled = true

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.encryption_mode == "aws:kms" ? local.effective_kms_key_arn : null
  }

  tags = merge(local.common_tags, {
    Name = local.control_table_name
  })

  lifecycle {
    prevent_destroy = true

    precondition {
      condition = (
        var.encryption_mode == "AES256"
        ? (!var.create_kms_key && var.kms_key_arn == null)
        : (var.create_kms_key != (var.kms_key_arn != null))
      )
      error_message = "AES256 must not configure a KMS key; aws:kms must configure exactly one of create_kms_key or kms_key_arn."
    }
  }
}
