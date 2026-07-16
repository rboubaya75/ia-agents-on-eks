check "storage_prefix_separation" {
  assert {
    condition     = !startswith("${var.storage.index_prefix}/", "${var.storage.source_prefix}/uploads/")
    error_message = "The durable index prefix must not descend from the temporary-upload prefix."
  }
}

check "mandatory_tags_not_overridden" {
  assert {
    condition     = length(setintersection(local.reserved_tag_keys, toset(keys(var.context.additional_tags)))) == 0
    error_message = "Additional tags must not redefine mandatory platform tag keys."
  }
}
