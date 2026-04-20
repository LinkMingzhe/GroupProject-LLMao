#!/usr/bin/env bash

set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-gitlab}"
GITLAB_BASE_URL="${GITLAB_BASE_URL:-http://localhost:8023}"

docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker run --name "${CONTAINER_NAME}" -d -p 8023:8023 \
  gitlab-populated-final-port8023 /opt/gitlab/embedded/bin/runsvdir-start

echo "Waiting 300 seconds for GitLab to boot..."
sleep 300

docker exec "${CONTAINER_NAME}" sed -i "s|^external_url.*|external_url '${GITLAB_BASE_URL}'|" /etc/gitlab/gitlab.rb
docker exec "${CONTAINER_NAME}" gitlab-ctl reconfigure

echo "GitLab is reset and configured at ${GITLAB_BASE_URL}."
