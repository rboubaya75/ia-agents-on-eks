variable "retained_vector_index_contracts" {
  description = "Previously active immutable vector-index contracts retained during migration."
  type = map(object({
    embedding_profile_alias    = string
    embedding_profile_revision = string
    embedding_dimensions       = number
    distance_metric            = string
    encryption_revision        = string
  }))
  default = {}
}
