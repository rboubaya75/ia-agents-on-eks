locals {
  name_stem      = "${var.context.resource_prefix}-${var.context.environment}"
  compact_region = replace(var.context.region, "-", "")

  document_bucket_name = "${local.name_stem}-${var.context.account_id}-${local.compact_region}-docs"
  lifecycle_rule_id    = "${local.name_stem}-temporary-uploads"

  reserved_tag_keys = toset([
    "Environment",
    "ManagedBy",
    "Project",
    "Workload",
    "Component",
    "Owner",
    "CostCenter",
    "DataClassification",
  ])

  common_tags = merge(
    var.context.additional_tags,
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
  )

  raw_capability_encryption_contracts = try(var.encryption.capability_overrides, {})
  capability_encryption_contracts = {
    for capability, contract in local.raw_capability_encryption_contracts :
    capability => contract if contract != null
  }
  storage_uses_encryption_override = contains(keys(local.capability_encryption_contracts), "storage")
}
