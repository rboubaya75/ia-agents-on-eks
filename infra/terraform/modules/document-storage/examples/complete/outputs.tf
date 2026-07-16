output "document_bucket_name" {
  description = "Document bucket created by the example."
  value       = module.document_storage.bucket_name
}

output "temporary_upload_prefix" {
  description = "Temporary-upload prefix."
  value       = module.document_storage.temporary_upload_prefix
}
