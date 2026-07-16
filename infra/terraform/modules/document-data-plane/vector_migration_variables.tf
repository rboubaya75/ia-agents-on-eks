variable "retained_vector_index_contracts" {
  description = "Previously active immutable vector-index contracts retained during re-indexing, cutover and stabilization. Keys are generation identifiers and must not equal vector_index_generation."
  type = map(object({
    embedding_profile_alias    = string
    embedding_profile_revision = string
    embedding_dimensions       = number
    distance_metric            = string
    encryption_revision        = string
  }))
  default = {}

  validation {
    condition = alltrue([
      for generation, contract in var.retained_vector_index_contracts :
      can(regex("^g[0-9]{3,6}$", generation)) &&
      can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", contract.embedding_profile_alias)) &&
      can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", contract.embedding_profile_revision)) &&
      contract.embedding_dimensions >= 1 &&
      contract.embedding_dimensions <= 4096 &&
      contains(["cosine", "euclidean"], contract.distance_metric) &&
      can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$", contract.encryption_revision))
    ])
    error_message = "Each retained vector-index contract must use a valid generation, embedding profile, dimension, distance metric and encryption revision."
  }

  validation {
    condition     = !contains(keys(var.retained_vector_index_contracts), var.vector_index_generation)
    error_message = "retained_vector_index_contracts must not contain the active vector_index_generation."
  }
}
