variable "context" {
  description = "Trusted non-secret deployment context used for deterministic naming and mandatory tags."
  type = object({
    resource_prefix     = string
    workload            = string
    component           = string
    project             = string
    environment         = string
    region              = string
    account_id          = string
    owner               = string
    cost_center         = string
    data_classification = string
    additional_tags     = optional(map(string), {})
  })

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,10}[a-z0-9]$", var.context.resource_prefix))
    error_message = "context.resource_prefix must contain 2 to 12 lowercase alphanumeric or hyphen characters."
  }

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,6}[a-z0-9]$", var.context.environment))
    error_message = "context.environment must contain 2 to 8 lowercase alphanumeric or hyphen characters."
  }

  validation {
    condition     = can(regex("^[0-9]{12}$", var.context.account_id))
    error_message = "context.account_id must contain exactly 12 digits."
  }
}

variable "encryption" {
  description = "Compatibility encryption default and optional capability-specific overrides."
  type = object({
    compatibility_default = object({
      mode                 = string
      create_customer_key  = bool
      existing_key_arn     = optional(string)
      deletion_window_days = number
    })
    capability_overrides = optional(map(object({
      mode                 = string
      create_customer_key  = bool
      existing_key_arn     = optional(string)
      deletion_window_days = number
    })), {})
  })

  validation {
    condition = alltrue([
      for capability in keys(try(var.encryption.capability_overrides, {})) :
      contains(["storage", "coordination", "messaging", "vector_store"], capability)
    ])
    error_message = "capability_overrides may contain only storage, coordination, messaging or vector_store."
  }
}

variable "storage" {
  description = "Document storage capability configuration."
  type = object({
    source_prefix                          = string
    index_prefix                           = string
    temporary_upload_expiration_days       = number
    abort_incomplete_multipart_upload_days = number
  })
}
