provider "aws" {
  region = var.aws_region
}

module "document_storage" {
  source = "../.."

  bucket_name       = var.bucket_name
  lifecycle_rule_id = var.lifecycle_rule_id
  encryption        = var.encryption
  tags              = var.tags
}
