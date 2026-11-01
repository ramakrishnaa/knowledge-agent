# Backend: one EC2 instance running the backend container from ECR.
# It is reachable only from CloudFront, which forwards /api/* to it on port 8000.

locals {
  backend_image = "${aws_ecr_repository.backend.repository_url}:latest" # scripts/deploy-backend pushes this tag
  backend_env = merge(
    {
      LLM_BASE_URL = var.llm_base_url
      LLM_API_KEY  = var.llm_api_key
      LLM_MODEL    = var.llm_model
    },
    var.extra_backend_env,
  )
}

# --- container registry ----------------------------------------------------------

resource "aws_ecr_repository" "backend" {
  name                 = "${var.project}-backend"
  image_tag_mutability = "MUTABLE"
  force_delete         = true

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 5 most recent images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 5 }
      action       = { type = "expire" }
    }]
  })
}

# --- configuration ---------------------------------------------------------------

# The whole backend .env file, read by the instance each time it (re)starts the container.
resource "aws_ssm_parameter" "backend_env" {
  name  = "/${var.project}/backend-env"
  type  = "SecureString"
  value = join("\n", [for k, v in local.backend_env : "${k}=${v}"])
}

# --- IAM -------------------------------------------------------------------------

resource "aws_iam_role" "backend" {
  name = "${var.project}-backend"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Session Manager shell access and remote commands, so no SSH key or open port 22 is needed.
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.backend.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "ecr_read" {
  role       = aws_iam_role.backend.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role_policy" "read_env" {
  name = "read-backend-env"
  role = aws_iam_role.backend.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = aws_ssm_parameter.backend_env.arn
      },
      {
        Effect    = "Allow"
        Action    = "kms:Decrypt"
        Resource  = "*"
        Condition = { StringEquals = { "kms:ViaService" = "ssm.${var.region}.amazonaws.com" } }
      },
    ]
  })
}

resource "aws_iam_instance_profile" "backend" {
  name = "${var.project}-backend"
  role = aws_iam_role.backend.name
}

# --- network ---------------------------------------------------------------------

data "aws_vpc" "default" {
  default = true
}

data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "backend" {
  name        = "${var.project}-backend"
  description = "Backend API, reachable only from CloudFront"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "API from CloudFront"
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    prefix_list_ids = [data.aws_ec2_managed_prefix_list.cloudfront.id]
  }

  egress {
    description = "LLM provider, ECR, SSM"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- instance --------------------------------------------------------------------

data "aws_ssm_parameter" "al2023_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_instance" "backend" {
  ami                    = data.aws_ssm_parameter.al2023_ami.value
  instance_type          = var.instance_type
  iam_instance_profile   = aws_iam_instance_profile.backend.name
  vpc_security_group_ids = [aws_security_group.backend.id]

  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    region        = var.region
    image         = local.backend_image
    env_parameter = aws_ssm_parameter.backend_env.name
  })

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_size = var.root_volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  tags = { Name = "${var.project}-backend" }

  lifecycle {
    # Documents live on this disk: don't replace the instance when a newer AMI is
    # published or the start-up script changes.
    ignore_changes = [ami, user_data]
  }
}

# A fixed address, so the public DNS name CloudFront uses as its origin survives stop/start.
resource "aws_eip" "backend" {
  instance = aws_instance.backend.id
  domain   = "vpc"
  tags     = { Name = "${var.project}-backend" }
}
