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

  vector_index_generations = var.vector_index_generations

  encryption_mode = var.encryption_mode
  create_kms_key  = var.create_kms_key
  kms_key_arn     = var.kms_key_arn
}
