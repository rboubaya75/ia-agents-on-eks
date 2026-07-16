output "document_control_table_name" {
  description = "DynamoDB control table name consumed by IA_DOCUMENT_CONTROL_TABLE."
  value       = aws_dynamodb_table.document_control.name
}

output "document_control_table_arn" {
  description = "DynamoDB control table ARN for Phase 4C workload policies."
  value       = aws_dynamodb_table.document_control.arn
}

output "document_bucket_name" {
  description = "Private document bucket name consumed by IA_DOCUMENT_BUCKET."
  value       = aws_s3_bucket.documents.bucket
}

output "document_bucket_arn" {
  description = "Private document bucket ARN for Phase 4C workload policies."
  value       = aws_s3_bucket.documents.arn
}

output "document_source_prefix" {
  description = "Root prefix consumed by IA_DOCUMENT_SOURCE_PREFIX."
  value       = var.document_source_prefix
}

output "document_index_prefix" {
  description = "Root chunk and manifest prefix consumed by IA_DOCUMENT_INDEX_PREFIX."
  value       = var.document_index_prefix
}

output "temporary_upload_prefix" {
  description = "Exact temporary-upload prefix covered by the one-day lifecycle rule."
  value       = local.temporary_upload_prefix
}

output "source_prefix" {
  description = "Immutable source prefix used by the application."
  value       = local.source_prefix
}

output "chunk_prefix" {
  description = "Chunk and vector-manifest prefix used by the application."
  value       = local.chunk_prefix
}

output "document_upload_lifecycle_rule_id" {
  description = "Lifecycle rule identifier consumed by IA_DOCUMENT_UPLOAD_LIFECYCLE_RULE_ID."
  value       = local.lifecycle_rule_id
}

output "document_kms_key_arn" {
  description = "Customer-managed KMS key ARN, or null when AWS-managed encryption is selected."
  value       = local.effective_kms_key_arn
}

output "document_max_source_bytes" {
  description = "Maximum source size consumed by IA_DOCUMENT_MAX_SOURCE_BYTES."
  value       = var.document_max_source_bytes
}

output "ingestion_queue_url" {
  description = "FIFO ingestion queue URL consumed by IA_DOCUMENT_INGESTION_QUEUE_URL."
  value       = aws_sqs_queue.ingestion.id
}

output "ingestion_queue_arn" {
  description = "FIFO ingestion queue ARN for Phase 4C workload policies."
  value       = aws_sqs_queue.ingestion.arn
}

output "ingestion_dlq_url" {
  description = "FIFO ingestion dead-letter queue URL."
  value       = aws_sqs_queue.ingestion_dlq.id
}

output "ingestion_dlq_arn" {
  description = "FIFO ingestion dead-letter queue ARN."
  value       = aws_sqs_queue.ingestion_dlq.arn
}

output "queue_visibility_timeout_seconds" {
  description = "Visibility timeout consumed by IA_DOCUMENT_QUEUE_VISIBILITY_TIMEOUT_SECONDS."
  value       = var.queue_visibility_timeout_seconds
}

output "ingestion_lease_ttl_seconds" {
  description = "Lease TTL consumed by IA_DOCUMENT_INGESTION_LEASE_TTL_SECONDS."
  value       = var.ingestion_lease_ttl_seconds
}

output "heartbeat_interval_seconds" {
  description = "Heartbeat interval consumed by IA_DOCUMENT_INGESTION_HEARTBEAT_INTERVAL_SECONDS."
  value       = var.heartbeat_interval_seconds
}

output "vector_bucket_name" {
  description = "S3 Vectors bucket name consumed by IA_VECTOR_BUCKET_NAME."
  value       = aws_s3vectors_vector_bucket.documents.vector_bucket_name
}

output "vector_bucket_arn" {
  description = "S3 Vectors bucket ARN for Phase 4C workload policies."
  value       = aws_s3vectors_vector_bucket.documents.vector_bucket_arn
}

output "vector_index_name" {
  description = "Active immutable S3 Vectors index generation consumed by IA_VECTOR_INDEX_NAME."
  value       = aws_s3vectors_index.documents[var.vector_index_generation].index_name
}

output "vector_index_arn" {
  description = "Active immutable S3 Vectors index ARN for Phase 4C workload policies."
  value       = aws_s3vectors_index.documents[var.vector_index_generation].index_arn
}

output "vector_indexes" {
  description = "All active and retained vector index generations managed during create-before-cutover migration."
  value = {
    for generation, index in aws_s3vectors_index.documents : generation => {
      name = index.index_name
      arn  = index.index_arn
    }
  }
}

output "vector_index_generation" {
  description = "Explicit active immutable index generation identifier."
  value       = var.vector_index_generation
}

output "embedding_profile_alias" {
  description = "Server-controlled active embedding profile alias."
  value       = var.embedding_profile_alias
}

output "embedding_profile_revision" {
  description = "Active immutable embedding profile revision."
  value       = var.embedding_profile_revision
}

output "embedding_dimensions" {
  description = "Active embedding vector dimension consumed by IA_EMBEDDING_DIMENSIONS."
  value       = var.embedding_dimensions
}

output "application_runtime_settings" {
  description = "Authoritative non-secret runtime settings for later Helm composition."
  value = {
    IA_DOCUMENT_CONTROL_TABLE                        = aws_dynamodb_table.document_control.name
    IA_DOCUMENT_BUCKET                               = aws_s3_bucket.documents.bucket
    IA_DOCUMENT_SOURCE_PREFIX                        = var.document_source_prefix
    IA_DOCUMENT_INDEX_PREFIX                         = var.document_index_prefix
    IA_DOCUMENT_KMS_KEY_ID                           = local.effective_kms_key_arn
    IA_DOCUMENT_MAX_SOURCE_BYTES                     = tostring(var.document_max_source_bytes)
    IA_DOCUMENT_UPLOAD_LIFECYCLE_RULE_ID             = local.lifecycle_rule_id
    IA_DOCUMENT_INGESTION_QUEUE_URL                  = aws_sqs_queue.ingestion.id
    IA_DOCUMENT_QUEUE_VISIBILITY_TIMEOUT_SECONDS     = tostring(var.queue_visibility_timeout_seconds)
    IA_DOCUMENT_INGESTION_LEASE_TTL_SECONDS          = tostring(var.ingestion_lease_ttl_seconds)
    IA_DOCUMENT_INGESTION_HEARTBEAT_INTERVAL_SECONDS = tostring(var.heartbeat_interval_seconds)
    IA_VECTOR_BUCKET_NAME                            = aws_s3vectors_vector_bucket.documents.vector_bucket_name
    IA_VECTOR_INDEX_NAME                             = aws_s3vectors_index.documents[var.vector_index_generation].index_name
    IA_EMBEDDING_PROFILE_ALIAS                       = var.embedding_profile_alias
    IA_EMBEDDING_PROFILE_REVISION                    = var.embedding_profile_revision
    IA_EMBEDDING_DIMENSIONS                          = tostring(var.embedding_dimensions)
  }
}
