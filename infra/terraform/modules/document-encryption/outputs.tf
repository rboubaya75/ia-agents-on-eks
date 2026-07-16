output "effective_key_arn" {
  description = "Customer-managed KMS key ARN, or null when AWS-managed encryption is selected."
  value       = local.effective_key_arn

  precondition {
    condition = (
      var.mode == "AES256"
      ? (!var.create_customer_key && var.existing_key_arn == null)
      : (var.create_customer_key != (var.existing_key_arn != null))
    )
    error_message = "AES256 must not configure a customer key; aws:kms must configure exactly one of create_customer_key or existing_key_arn."
  }
}

output "managed_key_arn" {
  description = "ARN of the module-managed key, or null when no key is created."
  value       = local.managed_key_arn
}

output "managed_alias_arn" {
  description = "ARN of the module-managed alias, or null when no alias is created."
  value       = try(aws_kms_alias.this[0].arn, null)
}

output "managed_alias_name" {
  description = "Name of the module-managed alias, or null when no alias is created."
  value       = try(aws_kms_alias.this[0].name, null)
}

output "contract" {
  description = "Stable non-secret encryption contract consumed by capability modules."
  value = {
    mode        = var.mode
    kms_key_arn = local.effective_key_arn
  }

  precondition {
    condition     = !var.create_customer_key || var.key_alias_name != null
    error_message = "key_alias_name is required when create_customer_key is true."
  }
}
