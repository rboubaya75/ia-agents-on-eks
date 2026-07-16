resource "aws_s3vectors_vector_bucket" "documents" {
  vector_bucket_name = local.vector_bucket_name
  force_destroy      = false

  encryption_configuration {
    sse_type    = var.encryption_mode
    kms_key_arn = var.encryption_mode == "aws:kms" ? local.effective_kms_key_arn : null
  }

  tags = merge(local.common_tags, {
    Name = local.vector_bucket_name
  })

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = length(local.vector_bucket_name) <= 63
      error_message = "The derived S3 Vectors bucket name exceeds 63 characters; shorten name_prefix or environment."
    }
  }
}

resource "aws_s3vectors_index" "documents" {
  vector_bucket_name = aws_s3vectors_vector_bucket.documents.vector_bucket_name
  index_name         = local.vector_index_name
  data_type          = "float32"
  dimension          = var.embedding_dimensions
  distance_metric    = var.vector_distance_metric

  encryption_configuration {
    sse_type    = var.encryption_mode
    kms_key_arn = var.encryption_mode == "aws:kms" ? local.effective_kms_key_arn : null
  }

  dynamic "metadata_configuration" {
    for_each = length(var.vector_non_filterable_metadata_keys) == 0 ? [] : [1]

    content {
      non_filterable_metadata_keys = sort(tolist(var.vector_non_filterable_metadata_keys))
    }
  }

  tags = merge(local.common_tags, {
    Name                     = local.vector_index_name
    EmbeddingProfileAlias    = var.embedding_profile_alias
    EmbeddingProfileRevision = var.embedding_profile_revision
    IndexGeneration          = var.vector_index_generation
    VectorEncryptionRevision = var.vector_encryption_revision
  })

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = local.embedding_alias_component != ""
      error_message = "embedding_profile_alias must contain at least one alphanumeric character after normalization."
    }

    precondition {
      condition     = length(local.vector_index_name) <= 63
      error_message = "The derived vector index name exceeds 63 characters."
    }

    precondition {
      condition     = length(setintersection(var.vector_non_filterable_metadata_keys, local.required_filterable_metadata_keys)) == 0
      error_message = "tenantId, classification, allowedRoles and generationId must remain filterable S3 Vectors metadata keys."
    }
  }
}
