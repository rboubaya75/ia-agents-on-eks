data "aws_caller_identity" "current" {}

locals {
  name_stem      = "${var.name_prefix}-${var.environment}"
  compact_region = replace(var.aws_region, "-", "")

  document_bucket_name = "${local.name_stem}-${data.aws_caller_identity.current.account_id}-${local.compact_region}-docs"
  vector_bucket_name   = "${local.name_stem}-${data.aws_caller_identity.current.account_id}-${local.compact_region}-vectors"
  control_table_name   = "${local.name_stem}-document-control"
  ingestion_queue_name = "${local.name_stem}-document-ingestion.fifo"
  ingestion_dlq_name   = "${local.name_stem}-document-ingestion-dlq.fifo"

  temporary_upload_prefix = "${var.document_source_prefix}/uploads/"
  source_prefix           = "${var.document_source_prefix}/sources/"
  chunk_prefix            = "${var.document_index_prefix}/"
  lifecycle_rule_id       = "${local.name_stem}-temporary-uploads"

  managed_kms_key_arn   = try(aws_kms_key.document[0].arn, null)
  effective_kms_key_arn = var.create_kms_key ? local.managed_kms_key_arn : var.kms_key_arn

  active_vector_index_contract = {
    embedding_profile_alias    = var.embedding_profile_alias
    embedding_profile_revision = var.embedding_profile_revision
    embedding_dimensions       = var.embedding_dimensions
    distance_metric            = var.vector_distance_metric
    encryption_revision        = var.vector_encryption_revision
  }

  vector_index_contracts = merge(
    var.retained_vector_index_contracts,
    {
      (var.vector_index_generation) = local.active_vector_index_contract
    },
  )

  vector_index_definitions = {
    for generation, contract in local.vector_index_contracts : generation => merge(contract, {
      generation = generation
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
          var.encryption_mode,
          contract.encryption_revision,
          coalesce(var.kms_key_arn, var.create_kms_key ? "module-managed" : "aws-managed"),
        ])),
        0,
        12,
      )
    })
  }

  vector_index_names = {
    for generation, contract in local.vector_index_definitions : generation =>
    "${local.name_stem}-${contract.alias_component}-${generation}-${contract.contract_hash}"
  }

  active_vector_index_name = local.vector_index_names[var.vector_index_generation]

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
