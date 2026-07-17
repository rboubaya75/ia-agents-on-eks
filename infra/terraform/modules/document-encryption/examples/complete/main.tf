provider "aws" {
  region = var.aws_region
}

module "document_encryption" {
  source = "../.."

  mode                = var.mode
  create_customer_key = var.create_customer_key
  existing_key_arn    = var.existing_key_arn
  key_alias_name      = var.key_alias_name
  key_description     = "Example document platform encryption key"
  tags                = var.tags
}
