resource "aws_kms_key" "this" {
  count = var.mode == "aws:kms" && var.create_customer_key ? 1 : 0

  description             = var.key_description
  deletion_window_in_days = var.deletion_window_days
  enable_key_rotation     = true
  key_usage               = "ENCRYPT_DECRYPT"
  multi_region            = var.multi_region
  tags                    = var.tags

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = var.key_alias_name != null
      error_message = "key_alias_name is required when create_customer_key is true."
    }
  }
}

resource "aws_kms_alias" "this" {
  count = var.mode == "aws:kms" && var.create_customer_key ? 1 : 0

  name          = coalesce(var.key_alias_name, "alias/invalid")
  target_key_id = aws_kms_key.this[0].key_id
}

locals {
  managed_key_arn   = try(aws_kms_key.this[0].arn, null)
  effective_key_arn = var.mode == "aws:kms" ? (var.create_customer_key ? local.managed_key_arn : var.existing_key_arn) : null
}
