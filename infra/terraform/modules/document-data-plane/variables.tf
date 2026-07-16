variable "name_prefix" {
  description = "Short lowercase prefix used for all document data-plane resources."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,10}[a-z0-9]$", var.name_prefix))
    error_message = "name_prefix must contain 2 to 12 lowercase alphanumeric or hyphen characters, start with a letter, and end with an alphanumeric character."
  }
}

variable "environment" {
  description = "Short lowercase environment identifier."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,6}[a-z0-9]$", var.environment))
    error_message = "environment must contain 2 to 8 lowercase alphanumeric or hyphen characters, start with a letter, and end with an alphanumeric character."
  }
}

variable "aws_region" {
  description = "AWS Region used by the environment provider and deterministic resource names."
  type        = string

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]+$", var.aws_region))
    error_message = "aws_region must be a valid AWS Region identifier."
  }
}

variable "tags" {
  description = "Tags applied to every taggable resource."
  type        = map(string)
  default     = {}
}

variable "document_source_prefix" {
  description = "Root S3 prefix used by the document source adapter."
  type        = string
  default     = "documents"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9/_-]{0,254}[A-Za-z0-9_-]$", var.document_source_prefix)) && !startswith(var.document_source_prefix, "/") && !endswith(var.document_source_prefix, "/")
    error_message = "document_source_prefix must be a non-empty relative S3 prefix without a leading or trailing slash."
  }
}

variable "document_index_prefix" {
  description = "Root S3 prefix used for chunks and vector-key manifests."
  type        = string
  default     = "rag"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9/_-]{0,254}[A-Za-z0-9_-]$", var.document_index_prefix)) && !startswith(var.document_index_prefix, "/") && !endswith(var.document_index_prefix, "/")
    error_message = "document_index_prefix must be a non-empty relative S3 prefix without a leading or trailing slash."
  }
}

variable "document_max_source_bytes" {
  description = "Maximum document source size accepted by the application."
  type        = number
  default     = 10000000

  validation {
    condition     = var.document_max_source_bytes >= 1 && var.document_max_source_bytes <= 50000000
    error_message = "document_max_source_bytes must be between 1 and 50,000,000 bytes."
  }
}

variable "temporary_upload_expiration_days" {
  description = "Retention of current temporary-upload versions. The application readiness contract requires exactly one day."
  type        = number
  default     = 1

  validation {
    condition     = var.temporary_upload_expiration_days == 1
    error_message = "temporary_upload_expiration_days must remain exactly 1 to satisfy the application readiness contract."
  }
}

variable "temporary_upload_noncurrent_expiration_days" {
  description = "Retention of noncurrent temporary-upload versions in the versioned document bucket."
  type        = number
  default     = 1

  validation {
    condition     = var.temporary_upload_noncurrent_expiration_days == 1
    error_message = "temporary_upload_noncurrent_expiration_days must remain exactly 1 so temporary object content is removed from the versioned bucket."
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

variable "queue_visibility_timeout_seconds" {
  description = "Default visibility timeout for ingestion messages."
  type        = number
  default     = 900

  validation {
    condition     = var.queue_visibility_timeout_seconds >= 30 && var.queue_visibility_timeout_seconds <= 43200
    error_message = "queue_visibility_timeout_seconds must be between 30 and 43,200."
  }
}

variable "ingestion_lease_ttl_seconds" {
  description = "DynamoDB ingestion lease TTL configured in the worker."
  type        = number
  default     = 900

  validation {
    condition     = var.ingestion_lease_ttl_seconds >= 30 && var.ingestion_lease_ttl_seconds <= 3600
    error_message = "ingestion_lease_ttl_seconds must be between 30 and 3,600."
  }
}

variable "heartbeat_interval_seconds" {
  description = "Worker lease and SQS visibility heartbeat interval."
  type        = number
  default     = 60

  validation {
    condition     = var.heartbeat_interval_seconds >= 1 && var.heartbeat_interval_seconds <= 300
    error_message = "heartbeat_interval_seconds must be between 1 and 300."
  }
}

variable "queue_message_retention_seconds" {
  description = "Ingestion queue message retention."
  type        = number
  default     = 345600

  validation {
    condition     = var.queue_message_retention_seconds >= 60 && var.queue_message_retention_seconds <= 1209600
    error_message = "queue_message_retention_seconds must be between 60 and 1,209,600."
  }
}

variable "dlq_message_retention_seconds" {
  description = "Dead-letter queue message retention."
  type        = number
  default     = 1209600

  validation {
    condition     = var.dlq_message_retention_seconds >= 60 && var.dlq_message_retention_seconds <= 1209600
    error_message = "dlq_message_retention_seconds must be between 60 and 1,209,600."
  }
}

variable "max_receive_count" {
  description = "Number of failed receives before SQS moves a message to the DLQ."
  type        = number
  default     = 5

  validation {
    condition     = var.max_receive_count >= 1 && var.max_receive_count <= 1000
    error_message = "max_receive_count must be between 1 and 1,000."
  }
}

variable "receive_wait_time_seconds" {
  description = "Long-poll wait time configured on the ingestion queue."
  type        = number
  default     = 20

  validation {
    condition     = var.receive_wait_time_seconds >= 1 && var.receive_wait_time_seconds <= 20
    error_message = "receive_wait_time_seconds must be between 1 and 20."
  }
}

variable "vector_index_generations" {
  description = "Retained immutable S3 Vectors index generations. Exactly one generation must be active."
  type = map(object({
    active                              = bool
    embedding_profile_alias             = string
    embedding_profile_revision          = string
    embedding_dimensions                = number
    vector_distance_metric              = string
    vector_encryption_revision          = string
    vector_non_filterable_metadata_keys = set(string)
  }))

  validation {
    condition     = length(var.vector_index_generations) >= 1
    error_message = "vector_index_generations must declare at least one retained index generation."
  }

  validation {
    condition     = alltrue([for generation in keys(var.vector_index_generations) : can(regex("^g[0-9]{3,6}$", generation))])
    error_message = "Each vector index generation key must use the form g001 through g999999."
  }

  validation {
    condition     = length([for generation, config in var.vector_index_generations : generation if config.active]) == 1
    error_message = "Exactly one vector index generation must set active = true."
  }

  validation {
    condition = alltrue([
      for config in values(var.vector_index_generations) :
      can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", config.embedding_profile_alias)) &&
      can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", config.embedding_profile_revision)) &&
      config.embedding_dimensions >= 1 &&
      config.embedding_dimensions <= 4096 &&
      contains(["cosine", "euclidean"], config.vector_distance_metric) &&
      can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$", config.vector_encryption_revision))
    ])
    error_message = "Each vector generation must define a valid embedding alias, immutable revision, dimension, distance metric and encryption revision."
  }

  validation {
    condition = alltrue(flatten([
      for config in values(var.vector_index_generations) : [
        for key in config.vector_non_filterable_metadata_keys :
        can(regex("^[A-Za-z][A-Za-z0-9]{0,62}$", key))
      ]
    ]))
    error_message = "Each non-filterable metadata key must contain 1 to 63 alphanumeric characters and start with a letter."
  }

  validation {
    condition = alltrue([
      for config in values(var.vector_index_generations) :
      length(setintersection(
        config.vector_non_filterable_metadata_keys,
        toset(["allowedRoles", "classification", "generationId", "tenantId"]),
      )) == 0
    ])
    error_message = "tenantId, classification, allowedRoles and generationId must remain filterable in every vector index generation."
  }
}

variable "encryption_mode" {
  description = "Encryption mode for DynamoDB, S3, SQS and S3 Vectors."
  type        = string
  default     = "AES256"

  validation {
    condition     = contains(["AES256", "aws:kms"], var.encryption_mode)
    error_message = "encryption_mode must be AES256 or aws:kms."
  }
}

variable "create_kms_key" {
  description = "Create a customer-managed KMS key inside this module. Valid only with encryption_mode aws:kms."
  type        = bool
  default     = false
}

variable "kms_key_arn" {
  description = "Existing customer-managed KMS key ARN. Mutually exclusive with create_kms_key."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition     = var.kms_key_arn == null || can(regex("^arn:[^:]+:kms:[^:]+:[0-9]{12}:key/[A-Za-z0-9-]+$", var.kms_key_arn))
    error_message = "kms_key_arn must be null or a KMS key ARN."
  }
}

variable "kms_deletion_window_days" {
  description = "Deletion window for a module-managed KMS key."
  type        = number
  default     = 30

  validation {
    condition     = var.kms_deletion_window_days >= 7 && var.kms_deletion_window_days <= 30
    error_message = "kms_deletion_window_days must be between 7 and 30."
  }
}
