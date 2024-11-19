#!/usr/bin/env bash
###
# File: init-docker-compose-stack-env.sh
# Project: scripts
# File Created: Wednesday, 20th November 2024 9:14:43 am
# Author: Josh5 (jsunnex@gmail.com)
# -----
# Last Modified: Wednesday, 20th November 2024 10:38:36 am
# Modified By: Josh5 (jsunnex@gmail.com)
###

# Input and output file paths
input_file="$(basename "${@:?}")"
input_file_path="$(cd "$(dirname "${@:?}")" && pwd)"
output_file="${input_file#docker-compose.}" # Remove 'docker-compose.' from filename
output_env_file="${output_file%.yml}.env"
output_shell_script_file="${output_file%.yml}.sh"

echo "Running stack init..."

# Extract configuration block
if grep -q "# <config_start>" "${input_file_path:?}/${input_file:?}"; then
    sed -n '/# <config_start>/,/# <config_end>/p' "${input_file_path:?}/${input_file:?}" | sed '/# <config_start>/d;/# <config_end>/d;s/^#   //' >"${input_file_path:?}/${output_env_file:?}"
    echo "--> Configuration extracted to: ${input_file_path:?}/${output_env_file:?}"
else
    echo "--> No configuration block found in ${input_file_path:?}/${input_file:?}"
fi

# Set defaults for local development in .env file
if [ -f "${input_file_path:?}/${output_env_file:?}" ]; then
    sed -i "s|<HOST_IP>|$(ip -4 addr show scope global | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -n1)|" "${input_file_path:?}/${output_env_file:?}"
    echo "--> Configuration modified with local development defaults"
fi

# Extract setup script block
if grep -q "# <script_start>" "${input_file_path:?}/${input_file:?}"; then
    echo "#!/usr/bin/env bash" >"${input_file_path:?}/${output_shell_script_file:?}"
    if [ -f "${input_file_path:?}/${output_env_file:?}" ]; then
        echo "source '${input_file_path:?}/${output_env_file:?}'" >>"${input_file_path:?}/${output_shell_script_file:?}"
    fi
    sed -n '/# <script_start>/,/# <script_end>/p' "${input_file_path:?}/${input_file:?}" | sed '/# <script_start>/d;/# <script_end>/d;s/^#   >//' >>"${input_file_path:?}/${output_shell_script_file:?}"
    chmod +x "${input_file_path:?}/${output_shell_script_file:?}"
    echo "--> Init script generated: ${input_file_path:?}/${output_shell_script_file:?}"
    echo '```'
    cat "${input_file_path:?}/${output_shell_script_file:?}"
    echo '```'
else
    echo "--> No setup script block found in ${input_file_path:?}/${input_file:?}"
fi

# Execute setup script
if [ -f "${input_file_path:?}/${output_shell_script_file:?}" ]; then
    pushd "${input_file_path:?}" >/dev/null || {
        echo "ERROR! Failed to change path to ${input_file_path:?}"
        exit 1
    }
    echo "--> Running init script ${input_file_path:?}/${output_shell_script_file:?}"
    "${input_file_path:?}/${output_shell_script_file:?}"
    echo
    popd >/dev/null || {
        echo "ERROR! Failed to change path from ${input_file_path:?}"
        exit 1
    }
fi

# Display command to run container
echo
echo "Run container with command:"
echo
echo "  > sudo docker compose -f '${input_file_path:?}/${input_file:?}' --env-file '${input_file_path:?}/${output_env_file:?}' up -d"
echo
