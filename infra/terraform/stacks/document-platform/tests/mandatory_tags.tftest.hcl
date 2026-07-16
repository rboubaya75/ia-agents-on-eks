mock_provider "aws" {}

run "reject_reserved_additional_tag" {
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
      additional_tags = {
        ManagedBy = "manual"
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

  expect_failures = [var.context]
}
