#!/bin/bash
set -e

# Define directories
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_FILES_DIR="${PROJECT_ROOT}/terraform/files"

echo "=== Building Agent Deployment Assets ==="

# 1. Ensure target directory exists
mkdir -p "${TERRAFORM_FILES_DIR}"

# 2. Serialize the Agent to a pickle file using python from venv
echo "1. Serializing Agent using cloudpickle..."
"${PROJECT_ROOT}/.venv/bin/python" -c "
import cloudpickle
from trip_planner.agent import root_agent
cloudpickle.dump(root_agent, open('${TERRAFORM_FILES_DIR}/agent_engine.pkl', 'wb'))
"
echo "   -> Saved agent_engine.pkl"

# 3. Create the dependencies archive (tar.gz) of trip_planner/
echo "2. Packaging trip_planner/ directory..."
tar -czf "${TERRAFORM_FILES_DIR}/dependencies.tar.gz" -C "${PROJECT_ROOT}" trip_planner
echo "   -> Saved dependencies.tar.gz"

echo "=== Build Complete! Ready for 'terraform apply' ==="
