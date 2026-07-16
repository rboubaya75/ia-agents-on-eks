module "encryption" {
  source   = "../../modules/document-encryption"
  for_each = local.encryption_contracts

  mode                 = each.value.mode
  create_customer_key  = each.value.create_customer_key
  existing_key_arn     = try(each.value.existing_key_arn, null)
  key_alias_name       = each.key == "compatibility" ? "alias/${local.name_stem}-document-data" : "alias/${local.name_stem}-${replace(each.key, "_", "-")}-data"
  key_description      = each.key == "compatibility" ? "${local.name_stem} document data-plane encryption" : "${local.name_stem} ${replace(each.key, "_", " ")} encryption"
  deletion_window_days = each.value.deletion_window_days
  tags = merge(local.common_tags, {
    Name = each.key == "compatibility" ? "${local.name_stem}-document-data" : "${local.name_stem}-${replace(each.key, "_", "-")}-data"
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
  encryption                             = module.encryption[local.storage_contract_name].contract
  tags                                   = local.common_tags
}
