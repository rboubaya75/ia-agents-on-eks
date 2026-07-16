resource "aws_kms_key" "document" {
  count = var.create_kms_key ? 1 : 0

  description             = "${local.name_stem} document data-plane encryption"
  deletion_window_in_days = var.kms_deletion_window_days
  enable_key_rotation     = true
  key_usage               = "ENCRYPT_DECRYPT"
  multi_region            = false

  tags = merge(local.common_tags, {
    Name = "${local.name_stem}-document-data"
  })

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = var.encryption_mode == "aws:kms"
      error_message = "create_kms_key requires encryption_mode to be aws:kms."
    }

    precondition {
      condition     = var.kms_key_arn == null
      error_message = "create_kms_key and kms_key_arn are mutually exclusive."
    }
  }
}

resource "aws_kms_alias" "document" {
  count = var.create_kms_key ? 1 : 0

  name          = "alias/${local.name_stem}-document-data"
  target_key_id = aws_kms_key.document[0].key_id
}
