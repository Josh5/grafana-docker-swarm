#!/usr/bin/env bash
###
# File: run.sh
# Project: tests
# File Created: Wednesday, 20th November 2024 11:44:29 am
# Author: Josh5 (jsunnex@gmail.com)
# -----
# Last Modified: Wednesday, 20th November 2024 11:53:15 am
# Modified By: Josh5 (jsunnex@gmail.com)
###

script_dir="$(cd "$(dirname "${BASH_SOURCE[@]}")" && pwd)"

sudo docker run --rm \
    --name test-fluent-bit \
    -e HOST_IP=$(hostname -I | awk '{print $1}') \
    -v "${script_dir:?}"/fluent-bit.yaml:/fluent-bit/etc/fluent-bit.yaml \
    fluent/fluent-bit:latest \
    /fluent-bit/bin/fluent-bit -c /fluent-bit/etc/fluent-bit.yaml
