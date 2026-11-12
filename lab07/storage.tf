# The weather tier's cache and history. On-demand billing, items expire by themselves, and no backups.
resource "aws_dynamodb_table" "weather" {
  name         = "${var.name_prefix}-weather-lookups"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "zip"

  attribute {
    name = "zip"
    type = "S"
  }

  ttl {
    attribute_name = "expiresAt"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = false
  }
}

# Terraform creates a placeholder. The AWS CLI installs the real key separately.
# A write-only value keeps the refreshed parameter value out of state.
resource "aws_ssm_parameter" "gemini" {
  name             = "/${var.name_prefix}/gemini-api-key"
  type             = "SecureString"
  value_wo         = "not-set-yet"
  value_wo_version = 1
}
