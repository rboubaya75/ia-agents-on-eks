resource "aws_s3vectors_vector_bucket" "documents" {
  vector_bucket_name = local.vector_bucket_name
  force_destroy      = false

  # Keep the vector bucket identity stable across index-level encryption migrations.
  # Every index declares its own immutable encryption contract below.
  encryption_configuration {
    sse_type = "AES256"
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

    precondition {
      condition     = !contains(keys(var.retained_vector_index_contracts), var.vector_index_generation)
      error_message = "retained_vector_index_contracts must not redefine the active vector_index_generation."
    }
  }
}

resource "aws_s3vectors_index" "documents" {
  for_each = local.vector_index_details

  vector_bucket_name = aws_s3vectors_vector_bucket.documents.vector_bucket_name
  index_name         = local.vector_index_names[each.key]
  data_type          = "float32"
  dimension          = each.value.contract.embedding_dimensions
  distance_metric    = each.value.contract.distance_metric

  encryption_configuration {
    sse_type    = each.value.contract.encryption_mode
    kms_key_arn = each.value.contract.encryption_mode == "aws:kms" ? each.value.contract.kms_key_arn : null
  }

  dynamic "metadata_configuration" {
    for_each = length(each.value.contract.non_filterable_metadata_keys) == 0 ? [] : [1]

    content {
      non_filterable_metadata_keys = sort(tolist(each.value.contract.non_filterable_metadata_keys))
    }
  }

  tags = merge(local.common_tags, {
    Name                     = local.vector_index_names[each.key]
    EmbeddingProfileAlias    = each.value.contract.embedding_profile_alias
    EmbeddingProfileRevision = each.value.contract.embedding_profile_revision
    IndexGeneration          = each.key
    VectorEncryptionRevision = each.value.contract.encryption_revision
  })

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = each.value.alias_component != ""
      error_message = "Each embedding profile alias must contain at least one alphanumeric character after normalization."
    }

    precondition {
      condition     = length(local.vector_index_names[each.key]) <= 63
      error_message = "A derived vector index name exceeds 63 characters."
    }

    precondition {
      condition     = length(setintersection(each.value.contract.non_filterable_metadata_keys, local.required_filterable_metadata_keys)) == 0
      error_message = "tenantId, classification, allowedRoles and generationId must remain filterable S3 Vectors metadata keys."
    }

    precondition {
      condition = (
        each.value.contract.encryption_mode == "AES256"
        ? each.value.contract.kms_key_arn == null
        : each.value.contract.encryption_mode == "aws:kms" && each.value.contract.kms_key_arn != null
      )
      error_message = "Each vector index must use AES256 without a KMS key or aws:kms with a KMS key ARN."
    }
  }
}
