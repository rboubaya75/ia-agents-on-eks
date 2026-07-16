locals {
  name_stem      = "${var.context.resource_prefix}-${var.context.environment}"
  compact_region = replace(var.context.region, "-", "")

  document_bucket_name = "${local.name_stem}-${var.context.account_id}-${local.compact_region}-docs"
  lifecycle_rule_id    = "${local.name_stem}-temporary-uploads"

  common_tags = merge(
    {
      Environment        = var.context.environment
      ManagedBy          = "Terraform"
      Project            = var.context.project
      Workload           = var.context.workload
      Component          = var.context.component
      Owner              = var.context.owner
      CostCenter         = var.context.cost_center
      DataClassification = var.context.data_classification
    },
    var.context.additional_tags,
  )

  encryption_contracts = merge(
    {
      compatibility = var.encryption.compatibility_default
    },
    try(var.encryption.capability_overrides, {}),
  )

  storage_contract_name = contains(keys(try(var.encryption.capability_overrides, {})), "storage") ? "storage" : "compatibility"
}
