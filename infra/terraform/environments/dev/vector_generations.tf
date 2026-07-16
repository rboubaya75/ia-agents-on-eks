variable "retained_vector_index_contracts" {
  description = "Previously active vector-index contracts retained during create-before-cutover migration."
  type = map(object({
    embedding_profile_alias      = string
    embedding_profile_revision   = string
    embedding_dimensions         = number
    distance_metric              = string
    encryption_mode              = string
    kms_key_arn                  = optional(string)
    encryption_revision          = string
    non_filterable_metadata_keys = optional(set(string), [
      "checksum",
      "chunkId",
      "embeddingDimensions",
      "embeddingModelId",
      "pipelineVersion",
      "sourceVersion",
    ])
  }))
  default = {}
}
