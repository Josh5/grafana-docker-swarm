#!/usr/bin/env bash
###
# File: run-logging-container.sh
# Project: fluent-bit
# File Created: Wednesday, 6th November 2024 5:20:00 pm
# Author: Josh5 (jsunnex@gmail.com)
# -----
# Last Modified: Monday, 23rd June 2025 3:30:53 am
# Modified By: Josh.5 (jsunnex@gmail.com)
###

_term() {
    sudo docker stop test-logging-container 2>/dev/null || true
    sudo docker rm test-logging-container 2>/dev/null || true
}
trap _term SIGTERM SIGINT

sudo docker stop test-logging-container 2>/dev/null || true
sudo docker rm test-logging-container 2>/dev/null || true
sudo docker run --rm --name test-logging-container \
   --log-driver=fluentd \
   --log-opt fluentd-address=localhost:34228 \
   --log-opt fluentd-request-ack="true" \
   --log-opt fluentd-async="false" \
   --log-opt tag=flb_glf.stdout_debug.test-service \
   --log-opt labels="source.env,source.service,source.version,source.project,source.account" \
   --label source.account="544038296934" \
   --label source.env="sandbox" \
   --label source.service=testing-service \
   --label source.version="1234" \
   --label source.project="manually-deployed" \
   ubuntu:latest \
   bash -c 'for i in {1..10}; do echo "{\"levelname\":\"INFO\",\"message\":\"Log Count $i\",\"time\":$(date +\"%Y-%m-%dT%H:%M:%S.%6N\")}"; sleep 5; done'
