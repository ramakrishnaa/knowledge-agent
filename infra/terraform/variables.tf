variable "region" {
  description = "AWS region for the backend, ECR repository and S3 bucket."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix for every resource. The ECR repository is <project>-backend."
  type        = string
  default     = "knowledge-agent"
}

variable "instance_type" {
  description = "EC2 instance type for the backend. Needs about 2 GB of memory for the embedding model."
  type        = string
  default     = "t3.small"
}

variable "root_volume_size" {
  description = "Root disk size in GB; holds Docker images and the ChromaDB data."
  type        = number
  default     = 20
}

variable "llm_api_key" {
  description = "API key for the LLM provider. Stored as an SSM SecureString (and in the Terraform state)."
  type        = string
  sensitive   = true
}

variable "llm_base_url" {
  description = "OpenAI-compatible endpoint."
  type        = string
  default     = "https://api.groq.com/openai/v1"
}

variable "llm_model" {
  description = "Model ID; must support tool calling."
  type        = string
  default     = "openai/gpt-oss-120b"
}

variable "extra_backend_env" {
  description = "Additional backend environment variables, e.g. AGENT_MAX_STEPS or tracing settings."
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "cloudfront_price_class" {
  description = "PriceClass_100 (North America and Europe) is the cheapest."
  type        = string
  default     = "PriceClass_100"
}
