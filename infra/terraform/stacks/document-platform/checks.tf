check "storage_prefix_separation" {
  assert {
    condition     = !startswith("${var.storage.index_prefix}/", "${var.storage.source_prefix}/uploads/")
    error_message = "The durable index prefix must not descend from the temporary-upload prefix."
  }
}

check "compatibility_contract_present" {
  assert {
    condition     = contains(keys(local.encryption_contracts), "compatibility")
    error_message = "The Phase 4B compatibility encryption contract must remain present during 4C-0."
  }
}
