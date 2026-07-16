mock_provider "aws" {}

variables {
  key_alias_name = "alias/iaagents-dev-document-data"
  tags = {
    Environment = "dev"
    ManagedBy   = "Terraform"
  }
}

run "aws_managed_default" {
  command = plan

  assert {
    condition     = length(aws_kms_key.this) == 0
    error_message = "AES256 must not create a customer-managed key."
  }

  assert {
    condition     = output.effective_key_arn == null
    error_message = "AES256 must expose a null KMS key ARN."
  }
}

run "module_managed_customer_key" {
  command = plan

  variables {
    mode                = "aws:kms"
    create_customer_key = true
  }

  assert {
    condition     = length(aws_kms_key.this) == 1 && length(aws_kms_alias.this) == 1
    error_message = "A module-managed contract must create one protected key and alias."
  }

  assert {
    condition     = aws_kms_key.this[0].enable_key_rotation
    error_message = "Customer-managed key rotation must remain enabled."
  }

  assert {
    condition     = output.managed_alias_name == "alias/iaagents-dev-document-data"
    error_message = "The module must preserve the supplied alias exactly."
  }
}

run "existing_customer_key" {
  command = plan

  variables {
    mode             = "aws:kms"
    existing_key_arn = "arn:aws:kms:eu-west-3:123456789012:key/11111111-2222-3333-4444-555555555555"
  }

  assert {
    condition     = length(aws_kms_key.this) == 0
    error_message = "An existing-key contract must not create a second key."
  }

  assert {
    condition     = output.effective_key_arn == "arn:aws:kms:eu-west-3:123456789012:key/11111111-2222-3333-4444-555555555555"
    error_message = "The effective contract must preserve the supplied existing key ARN."
  }
}

run "reject_missing_customer_key" {
  command = plan

  variables {
    mode = "aws:kms"
  }

  expect_failures = [output.effective_key_arn]
}

run "reject_conflicting_customer_keys" {
  command = plan

  variables {
    mode                = "aws:kms"
    create_customer_key = true
    existing_key_arn    = "arn:aws:kms:eu-west-3:123456789012:key/11111111-2222-3333-4444-555555555555"
  }

  expect_failures = [output.effective_key_arn]
}
