data "aws_caller_identity" "current" {}

locals {
  name_stem      = "${var.name_prefix}-${var.environment}"
  compact_region = replace(var.aws_region, "-", "")

  document_bucket_name = "${local.name_stem}-${data.aws_caller_identity.current.account_id}-${local.compact_region}-docs"
  vector_bucket_name   = "${local.name_stem}-${data.aws_caller_identity.current.account_id}-${local.compact_region}-vectors"
  control_table_name   = "${local.name_stem}-document-control"
  ingestion_queue_name = "${local.name_stem}-document-ingestion.fifo"
  ingestion_dlq_name   = "${local.name_stem}-document-ingestion-dlq.fifo"

  temporary_upload_prefix         = "${var.document_source_prefix}/uploads/"
  source_prefix                   = "${var.document_source_prefix}/sources/"
  chunk_prefix                    = "${var.document_index_prefix}/"
  lifecycle_rule_id               = "${local.name_stem}-temporary-uploads"
  lifecycle_delete_marker_rule_id = "${local.name_stem}-temporary-upload-delete-markers"

  managed_kms_key_arn   = try(aws_kms_key.document[0].arn, null)
  effective_kms_key_arn = var.create_kms_key ? local.managed_kms_key_arn : var.kms_key_arn

  vector_index_alias_components = {
    for generation, config in var.vector_index_generations :
    generation => substr(
      trim(replace(lower(config.embedding_profile_alias), "/[^a-z0-9-]/", "-"), "-"),
      0,
      16,
    )
  }

  vector_index_contract_hashes = {
    for generation, config in var.vector_index_generations :
    generation => substr(
      sha256(join(":", [
        config.embedding_profile_alias,
        config.embedding_profile_revision,
        tostring(config.embedding_dimensions),
        config.vector_distance_metric,
        generation,
        var.encryption_mode,
        config.vector_encryption_revision,
        coalesce(var.kms_key_arn, var.create_kms_key ? "module-managed" : "aws-managed"),
      ])),
      0,
      12,
    )
  }

  vector_index_names = {
    for generation, config in var.vector_index_generations :
    generation => "${local.name_stem}-${local.vector_index_alias_components[generation]}-${generation}-${local.vector_index_contract_hashes[generation]}"
  }

  vector_index_contracts = {
    for generation, config in var.vector_index_generations :
    generation => merge(config, {
      generation      = generation
      alias_component = local.vector_index_alias_components[generation]
      index_name      = local.vector_index_names[generation]
    })
  }

  active_vector_index_generation = try(one([
    for generation, config in var.vector_index_generations : generation if config.active
  ]), null)
  active_vector_index = try(
    local.vector_index_contracts[local.active_vector_index_generation],
    null,
  )

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
