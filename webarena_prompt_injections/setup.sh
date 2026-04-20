#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." &>/dev/null && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3.10}"
INSTALL_CLAUDE_DEMOS="${INSTALL_CLAUDE_DEMOS:-0}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Missing ${PYTHON_BIN}. Set PYTHON_BIN to a Python 3.10 executable, e.g. PYTHON_BIN=python3.10."
  exit 1
fi

setup_python_project() {
  local project_dir="$1"
  local requirements_file="$2"
  local editable_install="${3:-1}"

  echo "Setting up ${project_dir}"
  pushd "${project_dir}" >/dev/null

  "${PYTHON_BIN}" -m venv venv
  # shellcheck disable=SC1091
  source venv/bin/activate
  python -m pip install --upgrade pip wheel
  pip install -r "${requirements_file}"
  playwright install
  if [[ "${editable_install}" == "1" ]]; then
    pip install -e .
  fi
  deactivate

  popd >/dev/null
}

setup_python_project "${REPO_ROOT}/visualwebarena" "requirements.txt" "1"
setup_python_project "${REPO_ROOT}/webarena_prompt_injections" "requirements.txt" "0"

if [[ "${INSTALL_CLAUDE_DEMOS}" == "1" ]]; then
  for demo_dir in "${REPO_ROOT}/claude-35-computer-use-demo" "${REPO_ROOT}/claude-37-computer-use-demo"; do
    if [[ -f "${demo_dir}/dev-requirements.txt" ]]; then
      echo "Setting up ${demo_dir}"
      pushd "${demo_dir}" >/dev/null
      "${PYTHON_BIN}" -m venv .venv
      # shellcheck disable=SC1091
      source .venv/bin/activate
      python -m pip install --upgrade pip wheel
      pip install -r dev-requirements.txt
      deactivate
      popd >/dev/null
    fi
  done
fi

echo "Setup complete."
