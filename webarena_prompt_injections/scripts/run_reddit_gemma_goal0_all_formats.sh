#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../.." &>/dev/null && pwd)"
APP_DIR="${REPO_ROOT}/webarena_prompt_injections"

HOST="${HOST:-127.0.0.1}"
SHOPPING_URL="${SHOPPING_URL:-http://${HOST}:7770}"
GITLAB_URL="${GITLAB_URL:-http://${HOST}:8023}"
REDDIT_URL="${REDDIT_URL:-http://${HOST}:9999}"

MODEL="${MODEL:-gemini-3.1-pro-preview}"
STAGE3_MODEL="${STAGE3_MODEL:-gemini-2.5-flash}"
CUDA_DEVICE="${CUDA_DEVICE:-0}"
SYSTEM_PROMPT="${SYSTEM_PROMPT:-configs/system_prompts/wa_p_som_cot_id_actree_3s.json}"
CONFIG_PATH="${CONFIG_PATH:-configs/experiment_config.raw.json}"
GOAL_START="${GOAL_START:-0}"
GOAL_END="${GOAL_END:-0}"
OUTPUT_SUBDIR="${OUTPUT_SUBDIR:-reddit_gemini_3_1_pro}"
LAST_ATTEMPT_LOG=""

if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "Please export GEMINI_API_KEY before running this script."
  exit 1
fi

activate_env() {
  if [[ -f "${APP_DIR}/venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "${APP_DIR}/venv/bin/activate"
  fi
}

sleep_after_reset() {
  echo "[$(date -Is)] Waiting 60 seconds after reset..."
  sleep 60
}

reset_reddit() {
  echo "[$(date -Is)] Resetting reddit container..."
  docker rm -f forum >/dev/null 2>&1 || true
  docker run --name forum -d -p 9999:80 \
    -e RATELIMIT_WHITELIST=0.0.0.0/0,::/0 \
    postmill-populated-exposed-withimg >/dev/null
}

prepare_dirs() {
  mkdir -p "${APP_DIR}/output/${OUTPUT_SUBDIR}" "${APP_DIR}/output/wasp-smoke"
  echo "$$" > "${APP_DIR}/output/wasp-smoke/nohup.pid"
}

attempt_hit_quota_error() {
  local attempt_log="$1"
  grep -q "429 RESOURCE_EXHAUSTED" "${attempt_log}"
}

run_format_once() {
  local num="$1"
  local attempt="$2"
  local log_file="${APP_DIR}/output/${OUTPUT_SUBDIR}/reddit_gemini_goal0_format_${num}.log"
  local output_dir="${APP_DIR}/output/${OUTPUT_SUBDIR}/format_${num}"
  local attempt_log="${APP_DIR}/output/${OUTPUT_SUBDIR}/reddit_gemini_goal0_format_${num}.attempt_${attempt}.log"
  local status=0

  rm -rf "${output_dir}"
  : > "${attempt_log}"
  LAST_ATTEMPT_LOG="${attempt_log}"

  echo "[$(date -Is)] Running format ${num}, attempt ${attempt}..."

  (
    set -euo pipefail
    cd "${APP_DIR}"

    export DATASET="webarena_prompt_injections"
    export HOST="${HOST}"
    export SHOPPING="${SHOPPING_URL}"
    export GITLAB="${GITLAB_URL}"
    export REDDIT="${REDDIT_URL}"
    export STAGE3_MODEL="${STAGE3_MODEL}"

    CUDA_VISIBLE_DEVICES="${CUDA_DEVICE}" python run.py \
      --config "${CONFIG_PATH}" \
      --model "${MODEL}" \
      --system-prompt "${SYSTEM_PROMPT}" \
      --output-dir "${output_dir}" \
      --output-format webarena \
      --user_goal_start "${GOAL_START}" \
      --user_goal_end "${GOAL_END}" \
      --injection_format_idxs "${num}" \
      --site reddit
  ) >> "${attempt_log}" 2>&1 || status=$?

  cat "${attempt_log}" >> "${log_file}"
  return "${status}"
}

run_format_with_retry() {
  local num="$1"
  local log_file="${APP_DIR}/output/${OUTPUT_SUBDIR}/reddit_gemini_goal0_format_${num}.log"

  : > "${log_file}"
  echo "[$(date -Is)] ===== format ${num} start =====" | tee -a "${log_file}"

  reset_reddit
  sleep_after_reset

  if run_format_once "${num}" 1; then
    if attempt_hit_quota_error "${LAST_ATTEMPT_LOG}"; then
      echo "[$(date -Is)] Format ${num} hit 429 on attempt 1. Sleeping 60 seconds before rerunning the same format..." | tee -a "${log_file}"
      sleep 60
    else
      echo "[$(date -Is)] Format ${num} succeeded on attempt 1." | tee -a "${log_file}"
      return 0
    fi
  elif attempt_hit_quota_error "${LAST_ATTEMPT_LOG}"; then
    echo "[$(date -Is)] Format ${num} hit 429 on attempt 1. Sleeping 60 seconds before rerunning the same format..." | tee -a "${log_file}"
    sleep 60
  else
    echo "[$(date -Is)] Format ${num} failed on attempt 1. Waiting 60 seconds before retrying..." | tee -a "${log_file}"
    sleep 60
  fi

  if run_format_once "${num}" 2; then
    if attempt_hit_quota_error "${LAST_ATTEMPT_LOG}"; then
      echo "[$(date -Is)] Format ${num} hit 429 on attempt 2. Sleeping 60 seconds before rerunning the same format..." | tee -a "${log_file}"
      sleep 60
    else
      echo "[$(date -Is)] Format ${num} succeeded on attempt 2." | tee -a "${log_file}"
      return 0
    fi
  elif attempt_hit_quota_error "${LAST_ATTEMPT_LOG}"; then
    echo "[$(date -Is)] Format ${num} hit 429 on attempt 2. Sleeping 60 seconds before rerunning the same format..." | tee -a "${log_file}"
    sleep 60
  else
    echo "[$(date -Is)] Format ${num} failed on attempt 2. Resetting environment before final retry..." | tee -a "${log_file}"
    reset_reddit
    sleep_after_reset
  fi

  if run_format_once "${num}" 3; then
    if attempt_hit_quota_error "${LAST_ATTEMPT_LOG}"; then
      echo "[$(date -Is)] Format ${num} still hit 429 on attempt 3." | tee -a "${log_file}"
      return 1
    fi
    echo "[$(date -Is)] Format ${num} succeeded on attempt 3." | tee -a "${log_file}"
    return 0
  fi

  if attempt_hit_quota_error "${LAST_ATTEMPT_LOG}"; then
    echo "[$(date -Is)] Format ${num} failed on attempt 3 due to 429 quota exhaustion." | tee -a "${log_file}"
    return 1
  fi

  echo "[$(date -Is)] Format ${num} failed on attempt 3." | tee -a "${log_file}"
  return 1
}

main() {
  activate_env
  prepare_dirs

  cd "${APP_DIR}"
  for num in 0 1 2 3; do
    run_format_with_retry "${num}"
  done
}

main "$@"
