data "aws_caller_identity" "current" {}

locals {
  name_stem      = "${var.name_prefix}-${var.environment}"
  compact_region = replace(var.aws_region, "-", "")

  document_bucket_name = "${local.name_stem}-${data.aws_caller_identity.current.account_id}-${local.compact_region}-docs"
  vector_bucket_name   = "${local.name_stem}-${data.aws_caller_identity.current.account_id}-${local.compact_region}-vectors"
  control_table_name   = "${local.name_stem}-document-control"
  ingestion_queue_name = "${local.name_stem}-document-ingestion.fifo"
  ingestion_dlq_name   = "${local.name_stem}-document-ingestion-dlq.fifo"

  temporary_upload_prefix  = "${var.document_source_prefix}/uploads/"
  source_prefix            = "${var.document_source_prefix}/sources/"
  chunk_prefix             = "${var.document_index_prefix}/"
  lifecycle_rule_id        = "${local.name_stem}-temporary-uploads"
  lifecycle_marker_rule_id = "${local.lifecycle_rule_id}-delete-markers"

  managed_kms_key_arn   = try(aws_kms_key.document[0].arn, null)
  effective_kms_key_arn = var.create_kms_key ? local.managed_kms_key_arn : var.kms_key_arn

  active_vector_index_contract = {
    embedding_profile_alias      = var.embedding_profile_alias
    embedding_profile_revision   = var.embedding_profile_revision
    embedding_dimensions         = var.embedding_dimensions
    distance_metric              = var.vector_distance_metric
    encryption_mode              = var.encryption_mode
    kms_key_arn                  = var.encryption_mode == "aws:kms" ? local.effective_kms_key_arn : null
    encryption_revision          = var.vector_encryption_revision
    non_filterable_metadata_keys = var.vector_non_filterable_metadata_keys
  }

  vector_index_contracts = merge(
    var.retained_vector_index_contracts,
    {
      (var.vector_index_generation) = local.active_vector_index_contract
    },
  )

  vector_index_details = {
    for generation, contract in local.vector_index_contracts : generation => {
      contract = contract
      alias_component = substr(
        trim(replace(lower(contract.embedding_profile_alias), "/[^a-z0-9-]/", "-"), "-"),
        0,
        16,
      )
      contract_hash = substr(
        sha256(join(":", [
          contract.embedding_profile_alias,
          contract.embedding_profile_revision,
          tostring(contract.embedding_dimensions),
          contract.distance_metric,
          generation,
          contract.encryption_mode,
          contract.encryption_revision,
          coalesce(try(contract.kms_key_arn, null), "aws-managed"),
          join(",", sort(tolist(contract.non_filterable_metadata_keys))),
        ])),
        0,
        12,
      )
    }
  }

  vector_index_names = {
    for generation, details in local.vector_index_details : generation =>
    "${local.name_stem}-${details.alias_component}-${generation}-${details.contract_hash}"
  }

  required_filterable_metadata_keys = toset([
    "allowedRoles",
    "classification",
    "generationId",
    "tenantId",
  ])

  common_tags = merge(
    {
      Environment = var.environment
      ManagedBy   = "Terraform"
      Project     = "ia-agents-on-eks"
      Component   = "document-data-plane"
    },
    var.tags,
  )
}
