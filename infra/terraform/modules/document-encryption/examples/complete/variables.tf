variable "aws_region" {
  description = "AWS Region used by the example provider."
  type        = string
  default     = "eu-west-3"
}

variable "mode" {
  description = "Encryption mode used by the example."
  type        = string
  default     = "AES256"
}

variable "create_customer_key" {
  description = "Create a customer-managed key in the example."
  type        = bool
  default     = false
}

variable "existing_key_arn" {
  description = "Optional existing KMS key ARN."
  type        = string
  default     = null
  nullable    = true
}

variable "key_alias_name" {
  description = "Alias used when the example creates a key."
  type        = string
  default     = "alias/example-document-data"
}

variable "tags" {
  description = "Example tags."
  type        = map(string)
  default = {
    Environment = "example"
    ManagedBy   = "Terraform"
  }
}
