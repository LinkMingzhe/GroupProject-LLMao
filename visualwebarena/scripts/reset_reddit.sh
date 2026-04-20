#!/usr/bin/env bash

set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-forum}"

docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker run --name "${CONTAINER_NAME}" -p 9999:80 \
  -e RATELIMIT_WHITELIST=0.0.0.0/0,::/0 \
  -d postmill-populated-exposed-withimg

# Wait for the service to start accepting requests.
sleep 15
