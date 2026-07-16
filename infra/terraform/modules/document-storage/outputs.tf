output "bucket_name" {
  description = "Document bucket name."
  value       = aws_s3_bucket.this.bucket
}

output "bucket_arn" {
  description = "Document bucket ARN."
  value       = aws_s3_bucket.this.arn
}

output "source_prefix" {
  description = "Root source prefix."
  value       = var.source_prefix
}

output "index_prefix" {
  description = "Root chunk and manifest prefix."
  value       = var.index_prefix
}

output "temporary_upload_prefix" {
  description = "Temporary-upload prefix covered by the one-day lifecycle."
  value       = local.temporary_upload_prefix
}

output "immutable_source_prefix" {
  description = "Immutable source prefix."
  value       = local.immutable_source_prefix
}

output "chunk_prefix" {
  description = "Durable chunk and vector-manifest prefix."
  value       = local.chunk_prefix
}

output "lifecycle_rule_id" {
  description = "Temporary-upload lifecycle rule identifier."
  value       = var.lifecycle_rule_id
}

output "encryption" {
  description = "Effective storage encryption contract."
  value = {
    mode        = var.encryption.mode
    kms_key_arn = local.kms_key_arn
  }
}
