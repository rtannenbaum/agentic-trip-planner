# Retrieve Project Metadata (Used for project number lookup)
data "google_project" "project" {
  project_id = var.project_id
}

# 1. Google Project Service APIs Enablement

# Vertex AI API (Enables Reasoning Engine runtime)
resource "google_project_service" "aiplatform" {
  project            = var.project_id
  service            = "aiplatform.googleapis.com"
  disable_on_destroy = false
}

# Storage API (Enables artifact GCS buckets)
resource "google_project_service" "storage" {
  project            = var.project_id
  service            = "storage.googleapis.com"
  disable_on_destroy = false
}

# Secret Manager API (Enables secure API key storage)
resource "google_project_service" "secretmanager" {
  project            = var.project_id
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

# Agent Registry API (Enables agent registry and tracing visualizations)
resource "google_project_service" "agentregistry" {
  project            = var.project_id
  service            = "agentregistry.googleapis.com"
  disable_on_destroy = false
}

# Cloud Logging API (Enables container stdout/stderr log exports)
resource "google_project_service" "logging" {
  project            = var.project_id
  service            = "logging.googleapis.com"
  disable_on_destroy = false
}

# Telemetry API (Enables unified OpenTelemetry metrics and traces ingestion)
resource "google_project_service" "telemetry" {
  project            = var.project_id
  service            = "telemetry.googleapis.com"
  disable_on_destroy = false
}

# Cloud Trace API (Enables GCP Cloud Trace ingestion backend)
resource "google_project_service" "cloudtrace" {
  project            = var.project_id
  service            = "cloudtrace.googleapis.com"
  disable_on_destroy = false
}
# Model Armor API (Enables runtime AI protection templates)
resource "google_project_service" "modelarmor" {
  project            = var.project_id
  service            = "modelarmor.googleapis.com"
  disable_on_destroy = false
}

# Network Services API (Enables Agent Gateway proxy configurations)
resource "google_project_service" "networkservices" {
  project            = var.project_id
  service            = "networkservices.googleapis.com"
  disable_on_destroy = false
}


# 2. Custom Service Account for the Reasoning Engine

# Service Account (Dedicated running identity for the agent runtime container)
resource "google_service_account" "agent_sa" {
  account_id   = "reasoning-engine-test-sa"
  display_name = "Service Account for Reasoning Engine Test"
  project      = var.project_id
}


# 3. IAM Bindings for Tracing, Logging, Monitoring, and Vertex AI Execution

# GCS Bucket Access (Allows reading agent package & writing bookings database)
resource "google_storage_bucket_iam_member" "bucket_viewer" {
  bucket = google_storage_bucket.agent_bucket.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.agent_sa.email}"
}

# Logging Log Writer (Allows exporting runtime and diagnostic logs)
resource "google_project_iam_member" "log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

# Vertex AI User (Allows evaluations and execution of core ML actions)
resource "google_project_iam_member" "aiplatform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

# Cloud Trace Agent (Allows writing trace spans)
resource "google_project_iam_member" "trace_agent" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

# Monitoring Metric Writer (Allows writing evaluation/system metrics)
resource "google_project_iam_member" "monitoring_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}


# 4. Secret Manager Resources for the Gemini API Key

# Secret Creator (Creates secret container)
resource "google_secret_manager_secret" "api_key_secret" {
  provider  = google-beta
  project   = var.project_id
  secret_id = var.secret_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.secretmanager]
}

# Secret Accessor - Service Account (Allows reading the API key inside the agent)
resource "google_secret_manager_secret_iam_member" "sa_secret_accessor" {
  provider  = google-beta
  project   = var.project_id
  secret_id = google_secret_manager_secret.api_key_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.agent_sa.email}"
}

# Service Agent Identity (Retrieves Vertex AI system identity for deployment)
resource "google_project_service_identity" "aiplatform_sa" {
  provider = google-beta
  project  = var.project_id
  service  = "aiplatform.googleapis.com"
}

# Secret Accessor - Service Agent (Allows injecting the API key during container deployment)
resource "google_secret_manager_secret_iam_member" "service_agent_secret_accessor" {
  provider  = google-beta
  project   = var.project_id
  secret_id = google_secret_manager_secret.api_key_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_project_service_identity.aiplatform_sa.email}"
}

# Secret Accessor - Reasoning Engine Service Agent (Allows injecting the API key during container deployment)
resource "google_secret_manager_secret_iam_member" "re_service_agent_secret_accessor" {
  provider  = google-beta
  project   = var.project_id
  secret_id = google_secret_manager_secret.api_key_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"
}


# 5. GCS Bucket and Artifact Uploads for Agent Deployment

# Random Bucket Suffix (Prevents global bucket namespace collisions)
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# Artifact GCS Bucket (Houses requirements.txt, agent_engine.pkl, and dependencies)
resource "google_storage_bucket" "agent_bucket" {
  name                        = "${var.project_id}-agent-artifacts-${random_id.bucket_suffix.hex}"
  location                    = var.location
  project                     = var.project_id
  uniform_bucket_level_access = true
  force_destroy               = true

  depends_on = [google_project_service.storage]
}

# requirements.txt Upload (Uploads pinned python dependencies)
resource "google_storage_bucket_object" "requirements" {
  name   = "requirements-${filemd5("${path.module}/files/requirements.txt")}.txt"
  bucket = google_storage_bucket.agent_bucket.name
  source = "${path.module}/files/requirements.txt"
}

# agent_engine.pkl Upload (Uploads serialized cloudpickle agent)
resource "google_storage_bucket_object" "agent_pickle" {
  name   = "agent_engine-${filemd5("${path.module}/files/agent_engine.pkl")}.pkl"
  bucket = google_storage_bucket.agent_bucket.name
  source = "${path.module}/files/agent_engine.pkl"
}

# dependencies.tar.gz Upload (Uploads packed trip_planner MCP codebase)
resource "google_storage_bucket_object" "dependencies" {
  name   = "dependencies-${filemd5("${path.module}/files/dependencies.tar.gz")}.tar.gz"
  bucket = google_storage_bucket.agent_bucket.name
  source = "${path.module}/files/dependencies.tar.gz"
}


# 6. Defining the Vertex AI Reasoning Engine

# Reasoning Engine (Creates the Vertex AI Reasoning Engine using the ADK framework)
resource "google_vertex_ai_reasoning_engine" "test_engine" {
  provider     = google-beta
  project      = var.project_id
  region       = var.location
  display_name = var.display_name
  description  = "Test Reasoning Engine"

  spec {
    agent_framework = "google-adk"
    service_account = google_service_account.agent_sa.email
    agent_gateway   = google_network_services_agent_gateway.egress_gateway.id


    class_methods = jsonencode([
      {
        name        = "get_session"
        api_mode    = ""
        description = "Retrieve session by ID"
        parameters  = {
          type     = "object"
          required = ["user_id", "session_id"]
          properties = {
            user_id    = { type = "string" }
            session_id = { type = "string" }
          }
        }
      },
      {
        name        = "async_get_session"
        api_mode    = "async"
        description = "Retrieve session asynchronously by ID"
        parameters  = {
          type     = "object"
          required = ["user_id", "session_id"]
          properties = {
            user_id    = { type = "string" }
            session_id = { type = "string" }
          }
        }
      },
      {
        name        = "list_sessions"
        api_mode    = ""
        description = "List all sessions for a user"
        parameters  = {
          type     = "object"
          required = ["user_id"]
          properties = {
            user_id = { type = "string" }
          }
        }
      },
      {
        name        = "async_list_sessions"
        api_mode    = "async"
        description = "List all sessions for a user asynchronously"
        parameters  = {
          type     = "object"
          required = ["user_id"]
          properties = {
            user_id = { type = "string" }
          }
        }
      },
      {
        name        = "create_session"
        api_mode    = ""
        description = "Create a new session"
        parameters  = {
          type     = "object"
          required = ["user_id"]
          properties = {
            user_id    = { type = "string" }
            session_id = { type = "string" }
            state      = { type = "object" }
          }
        }
      },
      {
        name        = "async_create_session"
        api_mode    = "async"
        description = "Create a new session asynchronously"
        parameters  = {
          type     = "object"
          required = ["user_id"]
          properties = {
            user_id    = { type = "string" }
            session_id = { type = "string" }
            state      = { type = "object" }
          }
        }
      },
      {
        name        = "delete_session"
        api_mode    = ""
        description = "Delete session by ID"
        parameters  = {
          type     = "object"
          required = ["user_id", "session_id"]
          properties = {
            user_id    = { type = "string" }
            session_id = { type = "string" }
          }
        }
      },
      {
        name        = "async_delete_session"
        api_mode    = "async"
        description = "Delete session asynchronously by ID"
        parameters  = {
          type     = "object"
          required = ["user_id", "session_id"]
          properties = {
            user_id    = { type = "string" }
            session_id = { type = "string" }
          }
        }
      },
      {
        name        = "stream_query"
        api_mode    = "stream"
        description = "Stream queries from the agent"
        parameters  = {
          type     = "object"
          required = ["message", "user_id"]
          properties = {
            message    = { description = "Message string or object" }
            user_id    = { type = "string" }
            session_id = { type = "string" }
            run_config = { type = "object" }
          }
        }
      },
      {
        name        = "async_stream_query"
        api_mode    = "async_stream"
        description = "Stream queries asynchronously from the agent"
        parameters  = {
          type     = "object"
          required = ["message", "user_id"]
          properties = {
            message        = { description = "Message string or object" }
            user_id        = { type = "string" }
            session_id     = { type = "string" }
            session_events = { type = "array", items = { type = "object" } }
            run_config     = { type = "object" }
          }
        }
      },
      {
        name        = "streaming_agent_run_with_events"
        api_mode    = "async_stream"
        description = "Stream agent run with events asynchronously"
        parameters  = {
          type     = "object"
          required = ["request_json"]
          properties = {
            request_json = { type = "string" }
          }
        }
      }
    ])

    deployment_spec {
      env {
        name  = "BUCKET_NAME"
        value = google_storage_bucket.agent_bucket.name
      }
      env {
        name  = "FLASH_MODEL"
        value = var.flash_model
      }
      env {
        name  = "PRO_MODEL"
        value = var.pro_model
      }
      env {
        name  = "PATH"
        value = "/code/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
      }
      env {
        name  = "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY"
        value = "true"
      }
      env {
        name  = "OTEL_SEMCONV_STABILITY_OPT_IN"
        value = "gen_ai_latest_experimental"
      }
      env {
        name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
        value = "EVENT_ONLY"
      }
      env {
        name  = "OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED"
        value = "true"
      }
      env {
        name  = "OTEL_SERVICE_NAME"
        value = var.display_name
      }
      env {
        name  = "OTEL_RESOURCE_ATTRIBUTES"
        value = var.reasoning_engine_id != "" ? "service.name=${var.display_name},gcp.resource_type=vertex_ai_reasoning_engine,gcp.resource.id=projects/${var.project_id}/locations/${var.location}/reasoningEngines/${var.reasoning_engine_id}" : (var.agent_registry_id != "" ? "service.name=${var.display_name},agent.id=${var.agent_registry_id}" : "service.name=${var.display_name}")
      }
      secret_env {
        name = "GEMINI_API_KEY"

        secret_ref {
          secret  = google_secret_manager_secret.api_key_secret.secret_id
          version = "latest"
        }
      }
    }

    package_spec {
      requirements_gcs_uri     = "gs://${google_storage_bucket.agent_bucket.name}/${google_storage_bucket_object.requirements.name}"
      pickle_object_gcs_uri    = "gs://${google_storage_bucket.agent_bucket.name}/${google_storage_bucket_object.agent_pickle.name}"
      dependency_files_gcs_uri = "gs://${google_storage_bucket.agent_bucket.name}/${google_storage_bucket_object.dependencies.name}"
      python_version           = var.python_version
    }
  }

  depends_on = [
    google_project_service.aiplatform,
    google_project_service.cloudtrace,
    google_project_service.agentregistry,
    google_project_service.logging,
    google_project_service.telemetry,
    google_storage_bucket_iam_member.bucket_viewer,
    google_project_iam_member.log_writer,
    google_project_iam_member.trace_agent,
    google_project_iam_member.monitoring_writer,
    google_project_iam_member.aiplatform_user,
    google_secret_manager_secret_iam_member.sa_secret_accessor,
    google_secret_manager_secret_iam_member.service_agent_secret_accessor,
    google_secret_manager_secret_iam_member.re_service_agent_secret_accessor
  ]
}

# --- PII Protection & Agent Egress (Model Armor + Agent Gateway) ---

# PII Filter Template (Inspects and blocks prompts/responses violating sensitive rules)
resource "google_model_armor_template" "agent_pii_filter" {
  provider    = google-beta
  project     = var.project_id
  location    = var.location
  template_id = "agent-pii-filter-template"

  filter_config {
    sdp_settings {
      basic_config {
        filter_enforcement = "ENABLED"
      }
    }
  }

  template_metadata {
    custom_prompt_safety_error_code    = 400301
    custom_prompt_safety_error_message = "Blocked: Request violates PII safety constraints."
    log_sanitize_operations            = true
    log_template_operations            = true
    enforcement_type                   = "INSPECT_AND_BLOCK"
  }
}

# Egress Gateway (Governs and routes outbound tool calls)
resource "google_network_services_agent_gateway" "egress_gateway" {
  provider = google-beta
  project  = var.project_id
  location = var.location
  name     = "trip-planner-egress-gateway"

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }
}

# Authz Extension (Calls Model Armor service on egress proxy events)
resource "google_network_services_authz_extension" "egress_filter_ext" {
  provider  = google-beta
  project   = var.project_id
  location  = var.location
  name      = "trip-planner-egress-ext"
  service   = "modelarmor.${var.location}.rep.googleapis.com"
  authority = ""
  timeout   = "1s"
  fail_open = false

  metadata = {
    model_armor_settings = jsonencode([
      {
        request_template_id  = google_model_armor_template.agent_pii_filter.id
        response_template_id = google_model_armor_template.agent_pii_filter.id
      }
    ])
  }
}

# Authz Policy (Hooks egress gateway events to filter extension)
resource "google_network_security_authz_policy" "egress_security_policy" {
  provider = google-beta
  project  = var.project_id
  location = var.location
  name     = "trip-planner-egress-policy"

  target {
    resources = [google_network_services_agent_gateway.egress_gateway.id]
  }

  action         = "CUSTOM"
  policy_profile = "CONTENT_AUTHZ"

  custom_provider {
    authz_extension {
      resources = [google_network_services_authz_extension.egress_filter_ext.id]
    }
  }
}
