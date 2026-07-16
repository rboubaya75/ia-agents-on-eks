module "document_data_plane" {
  source = "../../modules/document-data-plane"

  name_prefix = var.name_prefix
  environment = "dev"
  aws_region  = var.aws_region
  tags        = var.tags

  document_source_prefix    = var.document_source_prefix
  document_index_prefix     = var.document_index_prefix
  document_max_source_bytes = var.document_max_source_bytes

  queue_visibility_timeout_seconds = var.queue_visibility_timeout_seconds
  ingestion_lease_ttl_seconds      = var.ingestion_lease_ttl_seconds
  heartbeat_interval_seconds       = var.heartbeat_interval_seconds

  embedding_profile_alias         = var.embedding_profile_alias
  embedding_profile_revision      = var.embedding_profile_revision
  embedding_dimensions            = var.embedding_dimensions
  vector_distance_metric          = var.vector_distance_metric
  vector_index_generation         = var.vector_index_generation
  vector_encryption_revision      = var.vector_encryption_revision
  retained_vector_index_contracts = var.retained_vector_index_contracts

  encryption_mode = var.encryption_mode
  create_kms_key  = var.create_kms_key
  kms_key_arn     = var.kms_key_arn
}
