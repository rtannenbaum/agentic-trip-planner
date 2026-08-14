variable "project_id" {
  description = "The ID of the project where the Reasoning Engine will be created."
  type        = string
}

variable "location" {
  description = "The GCP location for the Reasoning Engine and GCS bucket."
  type        = string
  default     = "us-central1"
}

variable "display_name" {
  description = "The display name for the Reasoning Engine."
  type        = string
  default     = "agent-platform-test"
}

variable "python_version" {
  description = "The python version for the Reasoning Engine."
  type        = string
  default     = "3.11"
}

variable "secret_id" {
  description = "The ID of the secret in Secret Manager."
  type        = string
  default     = "gemini-api-key"
}
