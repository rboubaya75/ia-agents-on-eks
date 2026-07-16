mock_provider "aws" {}

run "module_managed_compatibility_key" {
  command = plan

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
    }
    encryption = {
      compatibility_default = {
        mode                 = "aws:kms"
        create_customer_key  = true
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

  assert {
    condition     = output.document_kms_alias_name == "alias/iaagents-dev-document-data"
    error_message = "The compatibility key must preserve the exact Phase 4B KMS alias."
  }
}
