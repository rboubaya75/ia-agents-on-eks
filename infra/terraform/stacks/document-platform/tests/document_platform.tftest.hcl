mock_provider "aws" {}

variables {
  context = {
    resource_prefix     = "iaagents"
    workload            = "ia-agents"
    component           = "document-data-plane"
    project             = "ia-agents-on-eks"
    environment         = "dev"
    region              = "eu-west-3"
    account_id          = "123456789012"
    owner               = "platform"
    cost_center         = "platform"
    data_classification = "internal"
    additional_tags = {
      Contract = "phase-4c0b1"
    }
  }

  encryption = {
    compatibility_default = {
      mode                 = "AES256"
      create_customer_key  = false
      deletion_window_days = 30
    }
  }

  storage = {
    source_prefix                          = "documents"
    index_prefix                           = "rag"
    temporary_upload_expiration_days       = 1
    abort_incomplete_multipart_upload_days = 1
  }
}

run "phase_4b_compatible_names_and_outputs" {
  command = plan

  assert {
    condition     = output.document_bucket_name == "iaagents-dev-123456789012-euwest3-docs"
    error_message = "The preparatory stack must preserve the Phase 4B document bucket naming contract."
  }

  assert {
    condition     = output.temporary_upload_prefix == "documents/uploads/"
    error_message = "The stack must preserve the application temporary-upload contract."
  }

  assert {
    condition     = output.document_kms_key_arn == null
    error_message = "AES256 compatibility mode must expose no customer key ARN."
  }
}

run "storage_specific_existing_key_override" {
  command = plan

  variables {
    encryption = {
      compatibility_default = {
        mode                 = "AES256"
        create_customer_key  = false
        deletion_window_days = 30
      }
      capability_overrides = {
        storage = {
          mode                 = "aws:kms"
          create_customer_key  = false
          existing_key_arn     = "arn:aws:kms:eu-west-3:123456789012:key/11111111-2222-3333-4444-555555555555"
          deletion_window_days = 30
        }
      }
    }
  }

  assert {
    condition     = output.storage_encryption.mode == "aws:kms"
    error_message = "The stable stack contract must support a capability-specific encryption override."
  }
}
