#!/bin/bash
set -e

# Define directories
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_FILES_DIR="${PROJECT_ROOT}/terraform/files"

echo "=== Building Agent Deployment Assets ==="

# Read project config from terraform.tfvars if available
TFVARS_FILE="${PROJECT_ROOT}/terraform/terraform.tfvars"
PROJECT_ID=""
LOCATION=""
if [ -f "${TFVARS_FILE}" ]; then
  PROJECT_ID=$(grep -E '^\s*project_id\s*=' "${TFVARS_FILE}" | sed -E 's/.*=\s*"([^"]*)".*/\1/')
  LOCATION=$(grep -E '^\s*location\s*=' "${TFVARS_FILE}" | sed -E 's/.*=\s*"([^"]*)".*/\1/')
  FLASH_MODEL_VAR=$(grep -E '^\s*flash_model\s*=' "${TFVARS_FILE}" 2>/dev/null | sed -E 's/.*=\s*"([^"]*)".*/\1/' || true)
  PRO_MODEL_VAR=$(grep -E '^\s*pro_model\s*=' "${TFVARS_FILE}" 2>/dev/null | sed -E 's/.*=\s*"([^"]*)".*/\1/' || true)
fi

# Fallback to active gcloud project if not in tfvars
if [ -z "${PROJECT_ID}" ]; then
  PROJECT_ID=$(gcloud config get-value project 2>/dev/null || true)
fi

# Fail if project ID cannot be determined
if [ -z "${PROJECT_ID}" ]; then
  echo "ERROR: GCP Project ID could not be determined." >&2
  echo "Please set it in ${TFVARS_FILE} (project_id = \"...\") or configure your gcloud CLI: gcloud config set project <PROJECT_ID>" >&2
  exit 1
fi

export GOOGLE_CLOUD_PROJECT="${PROJECT_ID}"
export GOOGLE_CLOUD_LOCATION="${LOCATION:-us-central1}"
export FLASH_MODEL="${FLASH_MODEL_VAR:-gemini-2.5-flash}"
export PRO_MODEL="${PRO_MODEL_VAR:-gemini-2.5-pro}"

# 1. Ensure target directory exists
mkdir -p "${TERRAFORM_FILES_DIR}"

# 2. Serialize the Agent to a pickle file using python from venv
echo "1. Serializing Agent using cloudpickle..."
"${PROJECT_ROOT}/.venv/bin/python" -c "
import cloudpickle
from trip_planner.agent import root_agent, DynamicAdkApp, StructuredLoggingPlugin
app = DynamicAdkApp(agent=root_agent, enable_tracing=True, plugins=[StructuredLoggingPlugin()])
cloudpickle.dump(app, open('${TERRAFORM_FILES_DIR}/agent_engine.pkl', 'wb'))
"
echo "   -> Saved agent_engine.pkl"

# 3. Create the dependencies archive (tar.gz) of trip_planner/
echo "2. Packaging trip_planner/ directory..."
tar -czf "${TERRAFORM_FILES_DIR}/dependencies.tar.gz" -C "${PROJECT_ROOT}" trip_planner
echo "   -> Saved dependencies.tar.gz"

echo "=== Build Complete! Ready for 'terraform apply' ==="
