#!/usr/bin/env bash
###
# File: gen-bcrypt-pass.sh
# Project: scripts
# File Created: Friday, 24th January 2025 1:30:42 pm
# Author: Josh.5 (jsunnex@gmail.com)
# -----
# Last Modified: Tuesday, 13th January 2026 12:08:28 pm
# Modified By: Josh.5 (jsunnex@gmail.com)
###
script_path="$(cd "$(dirname "${BASH_SOURCE[@]}")" && pwd)"


pushd "${script_path:?}" >/dev/null || {
    echo "ERROR! Failed to change path to ${script_path:?}"
    exit 1
}

if [[ ! -d "venv" ]]; then
    python3 -m venv venv
fi
./venv/bin/python --version
./venv/bin/python -m pip install bcrypt

./venv/bin/python ${script_path:?}/gen-bcrypt-pass.py

popd >/dev/null || {
    echo "ERROR! Failed to change path from ${script_path:?}"
    exit 1
}
