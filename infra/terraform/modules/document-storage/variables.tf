variable "bucket_name" {
  description = "Final globally unique document bucket name supplied by the stack."
  type        = string

  validation {
    condition = (
      length(var.bucket_name) >= 3 &&
      length(var.bucket_name) <= 63 &&
      can(regex("^[a-z0-9][a-z0-9.-]*[a-z0-9]$", var.bucket_name)) &&
      !strcontains(var.bucket_name, "..")
    )
    error_message = "bucket_name must be a valid 3 to 63 character lowercase S3 bucket name."
  }
}

variable "source_prefix" {
  description = "Root prefix used by the document source adapter."
  type        = string
  default     = "documents"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9/_-]{0,254}[A-Za-z0-9_-]$", var.source_prefix)) && !startswith(var.source_prefix, "/") && !endswith(var.source_prefix, "/")
    error_message = "source_prefix must be a non-empty relative S3 prefix without a leading or trailing slash."
  }
}

variable "index_prefix" {
  description = "Root prefix used for durable chunks and vector manifests."
  type        = string
  default     = "rag"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9/_-]{0,254}[A-Za-z0-9_-]$", var.index_prefix)) && !startswith(var.index_prefix, "/") && !endswith(var.index_prefix, "/")
    error_message = "index_prefix must be a non-empty relative S3 prefix without a leading or trailing slash."
  }
}

variable "temporary_upload_expiration_days" {
  description = "Retention of current and noncurrent temporary-upload versions."
  type        = number
  default     = 1

  validation {
    condition     = var.temporary_upload_expiration_days == 1
    error_message = "temporary_upload_expiration_days must remain exactly 1 for the document API readiness contract."
  }
}

variable "abort_incomplete_multipart_upload_days" {
  description = "Days before incomplete multipart uploads are aborted."
  type        = number
  default     = 1

  validation {
    condition     = var.abort_incomplete_multipart_upload_days >= 1 && var.abort_incomplete_multipart_upload_days <= 7
    error_message = "abort_incomplete_multipart_upload_days must be between 1 and 7."
  }
}

variable "lifecycle_rule_id" {
  description = "Stable identifier for the temporary-upload lifecycle rule."
  type        = string

  validation {
    condition     = length(trimspace(var.lifecycle_rule_id)) >= 1 && length(var.lifecycle_rule_id) <= 255
    error_message = "lifecycle_rule_id must contain between 1 and 255 characters."
  }
}

variable "encryption" {
  description = "Explicit encryption contract supplied by the stack."
  type = object({
    mode        = string
    kms_key_arn = optional(string)
  })

  validation {
    condition = (
      contains(["AES256", "aws:kms"], var.encryption.mode) &&
      (
        var.encryption.mode == "AES256"
        ? try(var.encryption.kms_key_arn, null) == null
        : (
          try(var.encryption.kms_key_arn, null) != null &&
          can(regex("^arn:[^:]+:kms:[^:]+:[0-9]{12}:key/[A-Za-z0-9-]+$", var.encryption.kms_key_arn))
        )
      )
    )
    error_message = "AES256 must not set kms_key_arn; aws:kms requires a valid customer-managed key ARN."
  }
}

variable "tags" {
  description = "Final tags supplied by the stack."
  type        = map(string)
  default     = {}
}
