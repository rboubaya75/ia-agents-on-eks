terraform {
  backend "s3" {
    bucket = "workshop-team-stack-terraformrunnerterraformstateb-nzyzkinv9nvk"
    key    = "terraform.tfstate"
    region = "us-west-2"
  }
}
