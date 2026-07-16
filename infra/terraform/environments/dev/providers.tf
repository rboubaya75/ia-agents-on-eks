provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(
      {
        Environment = "dev"
        ManagedBy   = "Terraform"
        Project     = "ia-agents-on-eks"
      },
      var.tags,
    )
  }
}
