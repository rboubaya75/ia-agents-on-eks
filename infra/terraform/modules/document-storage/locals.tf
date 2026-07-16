locals {
  temporary_upload_prefix = "${var.source_prefix}/uploads/"
  immutable_source_prefix = "${var.source_prefix}/sources/"
  chunk_prefix            = "${var.index_prefix}/"
  kms_key_arn              = try(var.encryption.kms_key_arn, null)
}
