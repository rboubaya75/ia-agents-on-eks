output "document_bucket_name" {
  description = "Private document bucket name."
  value       = module.storage.bucket_name
}

output "document_bucket_arn" {
  description = "Private document bucket ARN."
  value       = module.storage.bucket_arn
}

output "document_source_prefix" {
  description = "Root source prefix."
  value       = module.storage.source_prefix
}

output "document_index_prefix" {
  description = "Root durable index prefix."
  value       = module.storage.index_prefix
}

output "temporary_upload_prefix" {
  description = "Temporary-upload prefix."
  value       = module.storage.temporary_upload_prefix
}

output "source_prefix" {
  description = "Immutable source prefix."
  value       = module.storage.immutable_source_prefix
}

output "chunk_prefix" {
  description = "Durable chunk and vector-manifest prefix."
  value       = module.storage.chunk_prefix
}

output "document_upload_lifecycle_rule_id" {
  description = "Temporary-upload lifecycle rule identifier."
  value       = module.storage.lifecycle_rule_id
}

output "document_kms_key_arn" {
  description = "Effective compatibility KMS key ARN, or null with AWS-managed encryption."
  value       = module.encryption.effective_key_arn
}

output "document_kms_alias_name" {
  description = "Compatibility KMS alias name, or null when no customer key is created."
  value       = module.encryption.managed_alias_name
}

output "storage_encryption" {
  description = "Effective storage encryption contract."
  value       = module.storage.encryption
}
