#!/usr/bin/env bash
###
# File: send-single-log.sh
# Project: tests
###

FORWARD_OUTPUT_HOST="${FORWARD_OUTPUT_HOST:-192.168.1.202}"
FORWARD_OUTPUT_PORT="${FORWARD_OUTPUT_PORT:-34224}"
FORWARD_OUTPUT_SHARED_KEY="${FORWARD_OUTPUT_SHARED_KEY:-123456789}"
FORWARD_OUTPUT_TLS="${FORWARD_OUTPUT_TLS:-on}"
FORWARD_OUTPUT_TLS_VERIFY="${FORWARD_OUTPUT_TLS_VERIFY:-off}"

EPOCH_SECONDS="$(date +%s)"
RFC3339_TIME="$(date -u +%Y-%m-%dT%H:%M:%S.%6NZ)"

echo "FORWARD_OUTPUT_HOST:              ${FORWARD_OUTPUT_HOST:?}"
echo "FORWARD_OUTPUT_PORT:              ${FORWARD_OUTPUT_PORT:?}"
echo "FORWARD_OUTPUT_SHARED_KEY:        ${FORWARD_OUTPUT_SHARED_KEY:?}"
echo "FORWARD_OUTPUT_TLS:               ${FORWARD_OUTPUT_TLS:?}"
echo "FORWARD_OUTPUT_TLS_VERIFY:        ${FORWARD_OUTPUT_TLS_VERIFY:?}"

sudo docker run --rm fluent/fluent-bit:latest \
  /fluent-bit/bin/fluent-bit \
  -i dummy \
    -p "dummy={\"level\":6,\"container_name\":\"/test-logging-container\",\"levelname\":\"info\",\"source_project\":\"manually-deployed\",\"source_version\":\"1234\",\"timestamp\":${EPOCH_SECONDS},\"service_name\":\"testing-service\",\"source_service\":\"testing-service\",\"source_account\":\"544038296934\",\"container_id\":\"1b5be6c727325117c4278c9f81a92bbc726e288805fd3f0f56a6d1f35466888a\",\"message\":\"Log Count ${EPOCH_SECONDS}\",\"time\":\"${RFC3339_TIME}\",\"source\":\"stdout\",\"source_env\":\"sandbox\"}" \
  -o forward \
    -p host="${FORWARD_OUTPUT_HOST}" \
    -p port="${FORWARD_OUTPUT_PORT}" \
    -p tls="${FORWARD_OUTPUT_TLS}" \
    -p tls.verify="${FORWARD_OUTPUT_TLS_VERIFY}" \
    -p shared_key="${FORWARD_OUTPUT_SHARED_KEY}" \
    -p self_hostname=test-fluentbit \
    -p tag=flb_glf.stdout_debug.test-service \
  -f 1


echo "DONE"
