variable "aws_region" {
  description = "AWS Region used by the example provider."
  type        = string
  default     = "eu-west-3"
}

variable "bucket_name" {
  description = "Globally unique example bucket name."
  type        = string
}

variable "lifecycle_rule_id" {
  description = "Stable lifecycle rule identifier."
  type        = string
  default     = "example-temporary-uploads"
}

variable "encryption" {
  description = "Example storage encryption contract."
  type = object({
    mode        = string
    kms_key_arn = optional(string)
  })
  default = {
    mode = "AES256"
  }
}

variable "tags" {
  description = "Example tags."
  type        = map(string)
  default = {
    Environment = "example"
    ManagedBy   = "Terraform"
  }
}
