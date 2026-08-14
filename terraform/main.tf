# Enable Vertex AI API (Reasoning Engine)
resource "google_project_service" "aiplatform" {
  project            = var.project_id
  service            = "aiplatform.googleapis.com"
  disable_on_destroy = false
}

# Enable Storage API
resource "google_project_service" "storage" {
  project            = var.project_id
  service            = "storage.googleapis.com"
  disable_on_destroy = false
}

# Enable Secret Manager API
resource "google_project_service" "secretmanager" {
  project            = var.project_id
  service            = "secretmanager.googleapis.com"
  disable_on_destroy = false
}

# Enable Agent Registry API
resource "google_project_service" "agentregistry" {
  project            = var.project_id
  service            = "agentregistry.googleapis.com"
  disable_on_destroy = false
}

# Random suffix for bucket name to avoid collisions
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# GCS Bucket to store agent artifacts
resource "google_storage_bucket" "agent_bucket" {
  name                        = "${var.project_id}-agent-artifacts-${random_id.bucket_suffix.hex}"
  location                    = var.location
  project                     = var.project_id
  uniform_bucket_level_access = true
  force_destroy               = true # Suitable for test config

  depends_on = [google_project_service.storage]
}

# Service Account for the Reasoning Engine
resource "google_service_account" "re_sa" {
  account_id   = "reasoning-engine-test-sa"
  display_name = "Service Account for Reasoning Engine Test"
  project      = var.project_id
}

# Grant SA admin access to GCS bucket to read and write bookings database
resource "google_storage_bucket_iam_member" "bucket_viewer" {
  bucket = google_storage_bucket.agent_bucket.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.re_sa.email}"
}

# Grant SA permission to write logs
resource "google_project_iam_member" "log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.re_sa.email}"
}

# Grant SA permission to use Vertex AI (needed if agent calls models)
resource "google_project_iam_member" "aiplatform_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.re_sa.email}"
}

# Create a secret for the API Key
resource "google_secret_manager_secret" "api_key_secret" {
  provider  = google-beta
  project   = var.project_id
  secret_id = var.secret_id

  replication {
    auto {}
  }

  depends_on = [google_project_service.secretmanager]
}

# Grant the Reasoning Engine SA access to the secret
resource "google_secret_manager_secret_iam_member" "sa_secret_accessor" {
  provider  = google-beta
  project   = var.project_id
  secret_id = google_secret_manager_secret.api_key_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.re_sa.email}"
}

# Retrieve the Vertex AI Service Agent Identity
resource "google_project_service_identity" "aiplatform_sa" {
  provider = google-beta
  project  = var.project_id
  service  = "aiplatform.googleapis.com"
}

# Grant the Vertex AI Service Agent access to the secret so it can inject it at deployment time
resource "google_secret_manager_secret_iam_member" "service_agent_secret_accessor" {
  provider  = google-beta
  project   = var.project_id
  secret_id = google_secret_manager_secret.api_key_secret.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_project_service_identity.aiplatform_sa.email}"
}

# Enable Cloud Trace API
resource "google_project_service" "cloudtrace" {
  project            = var.project_id
  service            = "cloudtrace.googleapis.com"
  disable_on_destroy = false
}

# Grant SA permission to write traces
resource "google_project_iam_member" "trace_agent" {
  project = var.project_id
  role    = "roles/cloudtrace.agent"
  member  = "serviceAccount:${google_service_account.re_sa.email}"
}

# Upload requirements.txt
resource "google_storage_bucket_object" "requirements" {
  name   = "requirements-${filemd5("${path.module}/files/requirements.txt")}.txt"
  bucket = google_storage_bucket.agent_bucket.name
  source = "${path.module}/files/requirements.txt"
}

# Upload agent pickle file
resource "google_storage_bucket_object" "agent_pickle" {
  name   = "agent_engine-${filemd5("${path.module}/files/agent_engine.pkl")}.pkl"
  bucket = google_storage_bucket.agent_bucket.name
  source = "${path.module}/files/agent_engine.pkl"
}

# Upload dependencies archive
resource "google_storage_bucket_object" "dependencies" {
  name   = "dependencies-${filemd5("${path.module}/files/dependencies.tar.gz")}.tar.gz"
  bucket = google_storage_bucket.agent_bucket.name
  source = "${path.module}/files/dependencies.tar.gz"
}

# Create Reasoning Engine
resource "google_vertex_ai_reasoning_engine" "test_engine" {
  provider     = google-beta
  project      = var.project_id
  region       = var.location
  display_name = var.display_name
  description  = "Test Reasoning Engine"

  spec {
    agent_framework = "google-adk"
    service_account = google_service_account.re_sa.email

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
    google_storage_bucket_iam_member.bucket_viewer,
    google_project_iam_member.log_writer,
    google_project_iam_member.trace_agent,
    google_project_iam_member.aiplatform_user,
    google_secret_manager_secret_iam_member.sa_secret_accessor,
    google_secret_manager_secret_iam_member.service_agent_secret_accessor
  ]
}
