resource "aws_sqs_queue" "ingestion_dlq" {
  name                        = local.ingestion_dlq_name
  fifo_queue                  = true
  content_based_deduplication = false
  message_retention_seconds   = var.dlq_message_retention_seconds

  sqs_managed_sse_enabled           = var.encryption_mode == "AES256" ? true : null
  kms_master_key_id                 = var.encryption_mode == "aws:kms" ? local.effective_kms_key_arn : null
  kms_data_key_reuse_period_seconds = var.encryption_mode == "aws:kms" ? 300 : null

  tags = merge(local.common_tags, {
    Name = local.ingestion_dlq_name
  })
}

resource "aws_sqs_queue" "ingestion" {
  name                        = local.ingestion_queue_name
  fifo_queue                  = true
  content_based_deduplication = false
  deduplication_scope         = "messageGroup"
  fifo_throughput_limit       = "perMessageGroupId"

  visibility_timeout_seconds = var.queue_visibility_timeout_seconds
  message_retention_seconds  = var.queue_message_retention_seconds
  receive_wait_time_seconds  = var.receive_wait_time_seconds

  sqs_managed_sse_enabled           = var.encryption_mode == "AES256" ? true : null
  kms_master_key_id                 = var.encryption_mode == "aws:kms" ? local.effective_kms_key_arn : null
  kms_data_key_reuse_period_seconds = var.encryption_mode == "aws:kms" ? 300 : null

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingestion_dlq.arn
    maxReceiveCount     = var.max_receive_count
  })

  tags = merge(local.common_tags, {
    Name = local.ingestion_queue_name
  })

  lifecycle {
    precondition {
      condition     = var.queue_visibility_timeout_seconds >= var.ingestion_lease_ttl_seconds
      error_message = "queue_visibility_timeout_seconds must be at least ingestion_lease_ttl_seconds."
    }

    precondition {
      condition     = var.heartbeat_interval_seconds * 2 <= var.ingestion_lease_ttl_seconds
      error_message = "heartbeat_interval_seconds must be no more than half the ingestion lease TTL."
    }

    precondition {
      condition     = var.heartbeat_interval_seconds * 2 <= var.queue_visibility_timeout_seconds
      error_message = "heartbeat_interval_seconds must be no more than half the SQS visibility timeout."
    }

    precondition {
      condition     = var.dlq_message_retention_seconds >= var.queue_message_retention_seconds
      error_message = "DLQ retention must be greater than or equal to ingestion queue retention."
    }
  }
}

resource "aws_sqs_queue_redrive_allow_policy" "ingestion_dlq" {
  queue_url = aws_sqs_queue.ingestion_dlq.id

  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns   = [aws_sqs_queue.ingestion.arn]
  })
}
