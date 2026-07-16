output "document_data_plane" {
  description = "Resource identifiers required by later workload identity and Helm phases."
  value = {
    document_control_table_name = module.document_data_plane.document_control_table_name
    document_control_table_arn  = module.document_data_plane.document_control_table_arn
    document_bucket_name        = module.document_data_plane.document_bucket_name
    document_bucket_arn         = module.document_data_plane.document_bucket_arn
    ingestion_queue_url         = module.document_data_plane.ingestion_queue_url
    ingestion_queue_arn         = module.document_data_plane.ingestion_queue_arn
    ingestion_dlq_url           = module.document_data_plane.ingestion_dlq_url
    ingestion_dlq_arn           = module.document_data_plane.ingestion_dlq_arn
    vector_bucket_name          = module.document_data_plane.vector_bucket_name
    vector_bucket_arn           = module.document_data_plane.vector_bucket_arn
    vector_indexes              = module.document_data_plane.vector_indexes
    active_vector_generation    = module.document_data_plane.vector_index_generation
    vector_index_name           = module.document_data_plane.vector_index_name
    vector_index_arn            = module.document_data_plane.vector_index_arn
    document_kms_key_arn        = module.document_data_plane.document_kms_key_arn
  }
}

output "application_runtime_settings" {
  description = "Authoritative non-secret settings for the Phase 4C Helm composition."
  value       = module.document_data_plane.application_runtime_settings
}
