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
  for_each = local.vector_index_definitions

  vector_bucket_name = aws_s3vectors_vector_bucket.documents.vector_bucket_name
  index_name         = local.vector_index_names[each.key]
  data_type          = "float32"
  dimension          = each.value.embedding_dimensions
  distance_metric    = each.value.distance_metric

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
    Name                     = local.vector_index_names[each.key]
    EmbeddingProfileAlias    = each.value.embedding_profile_alias
    EmbeddingProfileRevision = each.value.embedding_profile_revision
    IndexGeneration          = each.key
    VectorEncryptionRevision = each.value.encryption_revision
    ActiveIndex              = tostring(each.key == var.vector_index_generation)
  })

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = each.value.alias_component != ""
      error_message = "Every embedding profile alias must contain at least one alphanumeric character after normalization."
    }

    precondition {
      condition     = length(local.vector_index_names[each.key]) <= 63
      error_message = "A derived vector index name exceeds 63 characters."
    }

    precondition {
      condition     = length(setintersection(var.vector_non_filterable_metadata_keys, local.required_filterable_metadata_keys)) == 0
      error_message = "tenantId, classification, allowedRoles and generationId must remain filterable S3 Vectors metadata keys."
    }
  }
}
