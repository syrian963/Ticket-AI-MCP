# SPDX-License-Identifier: MIT
#
# Somewhere to put the eval report, and a way for CI to write there without a
# key living in the repository.
#
# The bucket is the small half. The half worth having is the role: GitHub
# Actions proves who it is with a short-lived OIDC token, so there is no
# AWS_SECRET_ACCESS_KEY in the repository secrets to leak, rotate or forget.
#
#   terraform init
#   terraform apply -var 'repository=syrian963/Ticket-AI-MCP'

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "repository" {
  description = "owner/name of the GitHub repository allowed to assume the role"
  type        = string

  validation {
    condition     = can(regex("^[^/]+/[^/]+$", var.repository))
    error_message = "repository has to be owner/name, so the trust policy cannot match half the world."
  }
}

variable "name" {
  description = "prefix for the bucket and the role"
  type        = string
  default     = "ticket-ai-evals"
}

variable "region" {
  type    = string
  default = "eu-central-1"
}

provider "aws" {
  region = var.region
}

data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------- the bucket

resource "aws_s3_bucket" "reports" {
  # Suffixed with the account id: bucket names are global, and a plain
  # "ticket-ai-evals" is already taken by somebody.
  bucket = "${var.name}-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket = aws_s3_bucket.reports.id

  # Nothing is served from the bucket directly. CloudFront reads it through an
  # origin access control, so every one of these stays on.
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "reports" {
  bucket = aws_s3_bucket.reports.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  rule {
    id     = "expire-old-reports"
    status = "Enabled"

    filter {
      prefix = "reports/"
    }

    # A report older than a quarter is not read again, and the baseline that
    # matters is committed in the repository anyway.
    expiration {
      days = 90
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }
}

# ------------------------------------------------------------- the delivery

resource "aws_cloudfront_origin_access_control" "reports" {
  name                              = "${var.name}-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "reports" {
  enabled             = true
  default_root_object = "index.html"
  comment             = "eval reports for ${var.repository}"

  origin {
    domain_name              = aws_s3_bucket.reports.bucket_regional_domain_name
    origin_id                = "reports"
    origin_access_control_id = aws_cloudfront_origin_access_control.reports.id
  }

  default_cache_behavior {
    target_origin_id       = "reports"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    # CachingOptimized, the AWS-managed policy.
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

resource "aws_s3_bucket_policy" "reports" {
  bucket = aws_s3_bucket.reports.id
  policy = data.aws_iam_policy_document.bucket.json
}

data "aws_iam_policy_document" "bucket" {
  statement {
    sid       = "CloudFrontRead"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.reports.arn}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }

    # Without this the statement would let any CloudFront distribution in any
    # account read the bucket.
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.reports.arn]
    }
  }
}

# ------------------------------------------------------ who may write to it

# One per account, so an existing provider is imported rather than duplicated:
#   terraform import aws_iam_openid_connect_provider.github <arn>
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Scoped to this repository. `repo:owner/*` would hand the role to every
    # repository the owner has, including one created later by someone else
    # with push access.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.repository}:*"]
    }
  }
}

resource "aws_iam_role" "publisher" {
  name               = "${var.name}-publisher"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "publish" {
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.reports.arn}/reports/*"]
  }

  statement {
    actions   = ["cloudfront:CreateInvalidation"]
    resources = [aws_cloudfront_distribution.reports.arn]
  }
}

resource "aws_iam_role_policy" "publish" {
  name   = "publish-reports"
  role   = aws_iam_role.publisher.id
  policy = data.aws_iam_policy_document.publish.json
}

# ------------------------------------------------------------------ outputs

output "role_arn" {
  description = "set this as the AWS_ROLE_ARN repository variable in GitHub"
  value       = aws_iam_role.publisher.arn
}

output "bucket" {
  value = aws_s3_bucket.reports.id
}

output "reports_url" {
  value = "https://${aws_cloudfront_distribution.reports.domain_name}/"
}
