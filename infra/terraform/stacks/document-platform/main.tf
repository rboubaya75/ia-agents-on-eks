module "encryption" {
  source = "../../modules/document-encryption"

  mode                 = var.encryption.compatibility_default.mode
  create_customer_key  = var.encryption.compatibility_default.create_customer_key
  existing_key_arn     = try(var.encryption.compatibility_default.existing_key_arn, null)
  key_alias_name       = "alias/${local.name_stem}-document-data"
  key_description      = "${local.name_stem} document data-plane encryption"
  deletion_window_days = var.encryption.compatibility_default.deletion_window_days
  tags = merge(local.common_tags, {
    Name = "${local.name_stem}-document-data"
  })
}

module "capability_encryption" {
  source   = "../../modules/document-encryption"
  for_each = local.capability_encryption_contracts

  mode                 = each.value.mode
  create_customer_key  = each.value.create_customer_key
  existing_key_arn     = try(each.value.existing_key_arn, null)
  key_alias_name       = "alias/${local.name_stem}-${replace(each.key, "_", "-")}-data"
  key_description      = "${local.name_stem} ${replace(each.key, "_", " ")} encryption"
  deletion_window_days = each.value.deletion_window_days
  tags = merge(local.common_tags, {
    Name = "${local.name_stem}-${replace(each.key, "_", "-")}-data"
  })
}

module "storage" {
  source = "../../modules/document-storage"

  bucket_name                            = local.document_bucket_name
  source_prefix                          = var.storage.source_prefix
  index_prefix                           = var.storage.index_prefix
  temporary_upload_expiration_days       = var.storage.temporary_upload_expiration_days
  abort_incomplete_multipart_upload_days = var.storage.abort_incomplete_multipart_upload_days
  lifecycle_rule_id                      = local.lifecycle_rule_id
  encryption = (
    local.storage_uses_encryption_override
    ? module.capability_encryption["storage"].contract
    : module.encryption.contract
  )
  tags = local.common_tags
}
