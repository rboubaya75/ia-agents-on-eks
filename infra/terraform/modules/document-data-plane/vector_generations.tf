variable "retained_vector_index_contracts" {
  description = "Previously active immutable vector-index contracts retained during create-before-cutover migrations. Map keys are generation identifiers."
  type = map(object({
    embedding_profile_alias       = string
    embedding_profile_revision    = string
    embedding_dimensions          = number
    distance_metric               = string
    encryption_mode               = string
    kms_key_arn                   = optional(string)
    encryption_revision           = string
    non_filterable_metadata_keys  = optional(set(string), [
      "checksum",
      "chunkId",
      "embeddingDimensions",
      "embeddingModelId",
      "pipelineVersion",
      "sourceVersion",
    ])
  }))
  default = {}

  validation {
    condition = alltrue([
      for generation, contract in var.retained_vector_index_contracts :
      can(regex("^g[0-9]{3,6}$", generation)) &&
      can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", contract.embedding_profile_alias)) &&
      can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", contract.embedding_profile_revision)) &&
      contract.embedding_dimensions >= 1 && contract.embedding_dimensions <= 4096 &&
      contains(["cosine", "euclidean"], contract.distance_metric) &&
      can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$", contract.encryption_revision))
    ])
    error_message = "Each retained vector index must use a valid generation, embedding contract, distance metric and encryption revision."
  }

  validation {
    condition = alltrue([
      for contract in values(var.retained_vector_index_contracts) :
      (
        contract.encryption_mode == "AES256"
        ? try(contract.kms_key_arn, null) == null
        : (
          contract.encryption_mode == "aws:kms" &&
          try(contract.kms_key_arn, null) != null &&
          can(regex("^arn:[^:]+:kms:[^:]+:[0-9]{12}:key/[A-Za-z0-9-]+$", contract.kms_key_arn))
        )
      )
    ])
    error_message = "A retained AES256 contract must not set kms_key_arn; a retained aws:kms contract must set a valid KMS key ARN."
  }

  validation {
    condition = alltrue(flatten([
      for contract in values(var.retained_vector_index_contracts) : [
        for key in contract.non_filterable_metadata_keys :
        can(regex("^[A-Za-z][A-Za-z0-9]{0,62}$", key))
      ]
    ]))
    error_message = "Each retained non-filterable metadata key must contain 1 to 63 alphanumeric characters and start with a letter."
  }
}
