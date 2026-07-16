variable "mode" {
  description = "Encryption mode exposed to consuming capabilities."
  type        = string
  default     = "AES256"

  validation {
    condition     = contains(["AES256", "aws:kms"], var.mode)
    error_message = "mode must be AES256 or aws:kms."
  }
}

variable "create_customer_key" {
  description = "Create and protect a customer-managed KMS key."
  type        = bool
  default     = false
}

variable "existing_key_arn" {
  description = "Existing customer-managed KMS key ARN. Mutually exclusive with create_customer_key."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.existing_key_arn == null || can(regex("^arn:[^:]+:kms:[^:]+:[0-9]{12}:key/[A-Za-z0-9-]+$", var.existing_key_arn))
    error_message = "existing_key_arn must be null or a valid KMS key ARN."
  }
}

variable "key_alias_name" {
  description = "Final KMS alias supplied by the stack when a key is created."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.key_alias_name == null || can(regex("^alias/[A-Za-z0-9/_-]{1,250}$", var.key_alias_name))
    error_message = "key_alias_name must be null or a valid KMS alias beginning with alias/."
  }
}

variable "key_description" {
  description = "Description applied to a module-managed customer key."
  type        = string
  default     = "Document platform encryption key"

  validation {
    condition     = length(trimspace(var.key_description)) >= 1 && length(var.key_description) <= 8192
    error_message = "key_description must contain between 1 and 8,192 characters."
  }
}

variable "deletion_window_days" {
  description = "KMS deletion waiting period for a module-managed customer key."
  type        = number
  default     = 30

  validation {
    condition     = var.deletion_window_days >= 7 && var.deletion_window_days <= 30
    error_message = "deletion_window_days must be between 7 and 30."
  }
}

variable "multi_region" {
  description = "Whether a module-managed KMS key is multi-Region."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Final tags supplied by the stack."
  type        = map(string)
  default     = {}
}
