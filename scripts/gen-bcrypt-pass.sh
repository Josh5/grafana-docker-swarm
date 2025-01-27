#!/usr/bin/env bash
###
# File: gen-bcrypt-pass.sh
# Project: scripts
# File Created: Friday, 24th January 2025 1:30:42 pm
# Author: Josh.5 (jsunnex@gmail.com)
# -----
# Last Modified: Friday, 24th January 2025 1:32:45 pm
# Modified By: Josh.5 (jsunnex@gmail.com)
###
script_path="$(cd "$(dirname "${BASH_SOURCE[@]}")" && pwd)"


pushd "${script_path:?}" >/dev/null || {
    echo "ERROR! Failed to change path to ${script_path:?}"
    exit 1
}

python -m venv venv
source ./venv/bin/activate
./venv/bin/python -m pip install bcrypt

./venv/bin/python ${script_path:?}/gen-bcrypt-pass.py

popd >/dev/null || {
    echo "ERROR! Failed to change path from ${script_path:?}"
    exit 1
}
