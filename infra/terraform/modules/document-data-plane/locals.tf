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

  embedding_alias_component = substr(
    trim(replace(lower(var.embedding_profile_alias), "/[^a-z0-9-]/", "-"), "-"),
    0,
    16,
  )
  vector_contract_hash = substr(
    sha256(join(":", [
      var.embedding_profile_alias,
      var.embedding_profile_revision,
      tostring(var.embedding_dimensions),
      var.vector_distance_metric,
      var.vector_index_generation,
      var.encryption_mode,
      var.vector_encryption_revision,
      coalesce(var.kms_key_arn, var.create_kms_key ? "module-managed" : "aws-managed"),
    ])),
    0,
    12,
  )
  vector_index_name = "${local.name_stem}-${local.embedding_alias_component}-${var.vector_index_generation}-${local.vector_contract_hash}"

  managed_kms_key_arn   = try(aws_kms_key.document[0].arn, null)
  effective_kms_key_arn = var.create_kms_key ? local.managed_kms_key_arn : var.kms_key_arn

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
