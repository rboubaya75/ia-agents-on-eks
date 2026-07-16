variable "aws_region" {
  description = "AWS Region for the development data plane."
  type        = string
}

variable "name_prefix" {
  description = "Short resource prefix."
  type        = string
  default     = "iaagents"
}

variable "tags" {
  description = "Additional environment tags."
  type        = map(string)
  default     = {}
}

variable "document_source_prefix" {
  description = "Root document source prefix."
  type        = string
  default     = "documents"
}

variable "document_index_prefix" {
  description = "Root chunk and manifest prefix."
  type        = string
  default     = "rag"
}

variable "document_max_source_bytes" {
  description = "Maximum source size accepted by the application."
  type        = number
  default     = 10000000
}

variable "queue_visibility_timeout_seconds" {
  description = "SQS visibility timeout."
  type        = number
  default     = 900
}

variable "ingestion_lease_ttl_seconds" {
  description = "Worker ingestion lease TTL."
  type        = number
  default     = 900
}

variable "heartbeat_interval_seconds" {
  description = "Worker heartbeat interval."
  type        = number
  default     = 60
}

variable "embedding_profile_alias" {
  description = "Server-controlled embedding profile alias."
  type        = string
}

variable "embedding_profile_revision" {
  description = "Immutable embedding profile revision."
  type        = string
}

variable "embedding_dimensions" {
  description = "Embedding vector dimension."
  type        = number
  default     = 1024
}

variable "vector_distance_metric" {
  description = "S3 Vectors distance metric."
  type        = string
  default     = "cosine"
}

variable "vector_index_generation" {
  description = "Explicit immutable vector index generation."
  type        = string
  default     = "g001"
}

variable "vector_encryption_revision" {
  description = "Immutable vector encryption contract revision."
  type        = string
  default     = "enc-v1"
}

variable "encryption_mode" {
  description = "AES256 or aws:kms."
  type        = string
  default     = "AES256"
}

variable "create_kms_key" {
  description = "Create a module-managed KMS key."
  type        = bool
  default     = false
}

variable "kms_key_arn" {
  description = "Existing KMS key ARN when create_kms_key is false."
  type        = string
  default     = null
  nullable    = true
}
