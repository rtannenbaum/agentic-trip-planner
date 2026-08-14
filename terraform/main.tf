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
    google_storage_bucket_iam_member.bucket_viewer,
    google_project_iam_member.log_writer,
    google_project_iam_member.aiplatform_user,
    google_secret_manager_secret_iam_member.sa_secret_accessor,
    google_secret_manager_secret_iam_member.service_agent_secret_accessor
  ]
}
