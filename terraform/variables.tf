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
  default     = "3.13"
}

variable "secret_id" {
  description = "The ID of the secret in Secret Manager."
  type        = string
  default     = "gemini-api-key"
}

variable "flash_model" {
  description = "The model name for flash agents on Vertex AI"
  type        = string
  default     = "gemini-2.5-flash"
}

variable "pro_model" {
  description = "The model name for pro agents on Vertex AI"
  type        = string
  default     = "gemini-2.5-pro"
}

variable "agent_registry_id" {
  description = "The exact Agent Registry resource ID (e.g. projects/<PROJECT>/locations/<LOCATION>/agents/<ID>) extracted from the Cloud Console view."
  type        = string
  default     = ""
}

variable "reasoning_engine_id" {
  description = "The exact Vertex AI Reasoning Engine resource ID (e.g. 8809438344590131200) to map OTel traces to the resource dashboard."
  type        = string
  default     = ""
}
