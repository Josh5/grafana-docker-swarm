#!/usr/bin/env bash
###
# File: entrypoint.sh
# Project: overlay
# File Created: Friday, 18th October 2024 5:05:51 pm
# Author: Josh5 (jsunnex@gmail.com)
# -----
# Last Modified: Sunday, 22nd June 2025 6:06:00 pm
# Modified By: Josh.5 (jsunnex@gmail.com)
###
set -eu

################################################
# --- Create Logging Function
#
print_log() {
    timestamp=$(date +'%Y/%m/%d %H:%M:%S')
    level="$1"
    shift
    message="$*"
    echo "[${timestamp}] [ ${level}] ${message}"
}

################################################
# --- Create Missing Directories
#
print_log "info" "Creating any missing directories."
mkdir -p \
    "${FLUENT_STORAGE_PATH:?}" \
    "${CERTIFICATES_DIRECTORY:?}"

################################################
# --- Create certificates
#
print_log "info" "Generating certificates in '${CERTIFICATES_DIRECTORY:?}'"
export CERTIFICATE_FILE_PATH="${CERTIFICATES_DIRECTORY:?}/fluent-bit.pem"
if [[ -n "${ENABLE_TLS_FORWARD_INPUT:-}" && "${ENABLE_TLS_FORWARD_INPUT,,}" =~ ^(true|t)$ ]]; then
    if [ -f "${CERTIFICATE_FILE_PATH:?}" ]; then
        print_log "info" "Checking expiration date on existing ${CERTIFICATE_FILE_PATH:?}"
        # Days before expiration to check
        DAYS_BEFORE_EXPIRATION=14
        # Get the expiration date of the certificate in seconds since epoch
        EXPIRATION_DATE=$(openssl x509 -enddate -noout -in "${CERTIFICATE_FILE_PATH:?}" | cut -d= -f2 || echo "Unable to load certificate")
        if [ "X${EXPIRATION_DATE:-}" = "X" ]; then
            # Invalid file
            print_log "info" "Certificate ${CERTIFICATE_FILE_PATH:?} appears to be invalid. Deleting..."
            rm -f "${CERTIFICATE_FILE_PATH:?}"
        else
            date -d "$(echo $EXPIRATION_DATE | sed "s/ GMT//")" +%s
            EXPIRATION_DATE_EPOCH=$(date -d "$(echo $EXPIRATION_DATE | sed "s/ GMT//")" +%s 2>/dev/null)
            # Get the current date in seconds since epoch
            CURRENT_DATE_EPOCH=$(date +%s)
            # Calculate the number of seconds in 14 days (14 * 86400)
            THRESHOLD=$((DAYS_BEFORE_EXPIRATION * 86400))
            # Check if the certificate will expire within the next 14 days
            if [ "$((EXPIRATION_DATE_EPOCH - CURRENT_DATE_EPOCH))" -lt "$THRESHOLD" ]; then
                # Not After date is earlier or equal to the current date (expired or expiring today)
                print_log "info" "Certificate ${CERTIFICATE_FILE_PATH:?} has expired or is expiring in the next 14 days. Deleting..."
                rm -f "${CERTIFICATE_FILE_PATH:?}"
            else
                print_log "info" "Certificate ${CERTIFICATE_FILE_PATH:?} is still valid until ${EXPIRATION_DATE:?}."
            fi
        fi
    fi

    if [[ -z "${USE_EXISTING_CERT:-}" || "${USE_EXISTING_CERT,,}" =~ ^(false|f)$ ]]; then
        print_log "info" "Configured to not use an existing cert."
    else
        if [ -f "${EXISTING_KEY_PATH:-}" ] && [ -f "${EXISTING_CERT_PATH:-}" ]; then
            print_log "info" "Using supplied ${EXISTING_KEY_PATH:?} and ${EXISTING_CERT_PATH:?} files to create ${CERTIFICATE_FILE_PATH:?}."
            cat ${EXISTING_KEY_PATH:?} ${EXISTING_CERT_PATH:?} >"${CERTIFICATE_FILE_PATH:?}"
        else
            print_log "info" "Configured to use an existing cert, but no EXISTING_KEY_PATH variable configured or the path in the variable EXISTING_KEY_PATH does not exsist."
        fi
    fi

    if [ ! -f "${CERTIFICATE_FILE_PATH:?}" ]; then
        print_log "info" "Certificate ${CERTIFICATE_FILE_PATH:?} does not exist. Creating a new one."
        if [ "X${CERT_FQDN:-}" != "X" ]; then
            HOST_HOSTNAME="${CERT_FQDN:?}"
        fi
        if [[ -n "${USE_CERTBOT_TO_GENERATE_KEY:-}" && "${USE_CERTBOT_TO_GENERATE_KEY,,}" =~ ^(true|t)$ ]]; then
            print_log "info" "Waiting for Nginx proxy container..."
            sleep 5
            i=1
            while [ $i -le 60 ]; do
                if [ -f "/var/www/certbot/.proxy-running" ]; then
                    print_log "info" "  - The Nginx proxy container is running"
                    rm -f "/var/www/certbot/.proxy-running"
                    break
                fi
                print_log "info" "  - Nginx proxy container check #$i - Not yet running. Recheck in 5 seconds..."
                sleep 5
                i=$((i + 1))
            done
            # Sleep here to wait long enough to ensure nginx is running
            print_log "info" "Pausing startup for 10 seconds to ensure Nginx service has completed startup for certbot certifiacte creation..."
            sleep 10
            echo

            print_log "info" "Running certbot command..."
            rm -rf "${CERTIFICATES_DIRECTORY:?}"/letsencrypt
            if certbot certonly \
                --webroot \
                --webroot-path /var/www/certbot \
                -d "${HOST_HOSTNAME:?}" \
                --email "${CERT_EMAIL:?}" \
                --agree-tos \
                --no-eff-email \
                --non-interactive \
                --config-dir "${CERTIFICATES_DIRECTORY:?}/letsencrypt/etc" \
                --logs-dir "${CERTIFICATES_DIRECTORY:?}/letsencrypt/logs" \
                --work-dir "${CERTIFICATES_DIRECTORY:?}/letsencrypt/work"; then

                cat \
                    "${CERTIFICATES_DIRECTORY:?}/letsencrypt/etc/live/${CERT_FQDN:?}/fullchain.pem" \
                    "${CERTIFICATES_DIRECTORY:?}/letsencrypt/etc/live/${CERT_FQDN:?}/privkey.pem" \
                    >"${CERTIFICATE_FILE_PATH:?}"
            else
                print_log "error" "Certbot failed to obtain certificate. Sleeping for 10 minutes before exiting."
                sleep 600
                exit 1
            fi
        else
            print_log "info" "Creating self-signed certificate ${CERTIFICATE_FILE_PATH:?}..."
            openssl req -new -x509 \
                -days 1095 \
                -newkey rsa:4096 \
                -sha256 \
                -nodes \
                -keyout "${CERTIFICATE_FILE_PATH:?}" \
                -out "${CERTIFICATE_FILE_PATH:?}" \
                -subj "/CN=${HOST_HOSTNAME:?}"
        fi
    fi
fi

################################################
# --- Configure Fluent-bit
#
mkdir -p /etc/fluent-bit-custom
cp -rf /etc/fluent-bit/* /etc/fluent-bit-custom/
touch /etc/fluent-bit-custom/parsers.conf
touch /etc/fluent-bit-custom/plugins.conf

# Specify a ** match in single quotes if no prefix was provided
output_tag_match="${FLUENT_BIT_TAG_PREFIX:-}**"
if [ -z "${FLUENT_BIT_TAG_PREFIX:-}" ]; then
    output_tag_match="'**'"
fi

# Fluent HTTP Input
if [[ "${ENABLE_HTTP_INPUT,,}" =~ ^(t|true)$ ]]; then
    print_log "info" "Adding HTTP input"
    yaml_file="fluent-bit.http.input.yaml"
    cat <<EOF >/etc/fluent-bit-custom/${yaml_file:?}
pipeline:
  inputs:
    # HTTP input to sit behind an LB
    - name: http
      listen: 0.0.0.0
      port: ${HTTP_INPUT_PORT:?}
      storage.type: filesystem
      buffer_chunk_size: 5M
      buffer_max_size: 1000M
      threaded: true
EOF
    sed -i "s/^\(\s*\)#-\( ${yaml_file:?}\)/\1- ${yaml_file:?}/" /etc/fluent-bit-custom/fluent-bit.yaml
    echo
    echo /etc/fluent-bit-custom/${yaml_file:?}
    cat /etc/fluent-bit-custom/${yaml_file:?}
else
    print_log "info" "Leaving HTTP input disabled"
fi

# Fluent Forward TLS Input
if [[ "${ENABLE_TLS_FORWARD_INPUT,,}" =~ ^(t|true)$ ]]; then
    print_log "info" "Adding TLS Forward input"
    yaml_file="fluent-bit.tls-forward.input.yaml"
    cat <<EOF >/etc/fluent-bit-custom/${yaml_file:?}
pipeline:
  inputs:
    # TLS Forward input
    - name: forward
      listen: 0.0.0.0
      port: ${TLS_FORWARD_INPUT_PORT:?}
      shared_key: ${TLS_FORWARD_INPUT_SHARED_KEY:-}
      self_hostname: ${HOST_HOSTNAME:?}
      storage.type: filesystem
      buffer_chunk_size: 5M
      buffer_max_size: 1000M
      tls: on
      tls.verify: ${TLS_FORWARD_INPUT_VERIFY:-off}
      tls.key_file: ${CERTIFICATES_DIRECTORY:?}/fluent-bit.pem
      tls.crt_file: ${CERTIFICATES_DIRECTORY:?}/fluent-bit.pem
      threaded: true
EOF
    sed -i "s/^\(\s*\)#-\( ${yaml_file:?}\)/\1- ${yaml_file:?}/" /etc/fluent-bit-custom/fluent-bit.yaml
    echo
    echo /etc/fluent-bit-custom/${yaml_file:?}
    cat /etc/fluent-bit-custom/${yaml_file:?}
else
    print_log "info" "Leaving TLS Forward input disabled"
fi

# Fluent Forward PT Input
if [[ "${ENABLE_PT_FORWARD_INPUT,,}" =~ ^(t|true)$ ]]; then
    print_log "info" "Adding PT Forward input"
    yaml_file="fluent-bit.pt-forward.input.yaml"
    cat <<EOF >/etc/fluent-bit-custom/${yaml_file:?}
pipeline:
  inputs:
    # PT Forward input
    - name: forward
      listen: 0.0.0.0
      port: ${PT_FORWARD_INPUT_PORT:?}
      shared_key: ${PT_FORWARD_INPUT_SHARED_KEY:-}
      self_hostname: ${HOST_HOSTNAME:?}
      storage.type: filesystem
      buffer_chunk_size: 5M
      buffer_max_size: 1000M
      tls: off
      tls.verify: off
      threaded: true
EOF
    sed -i "s/^\(\s*\)#-\( ${yaml_file:?}\)/\1- ${yaml_file:?}/" /etc/fluent-bit-custom/fluent-bit.yaml
    echo
    echo /etc/fluent-bit-custom/${yaml_file:?}
    cat /etc/fluent-bit-custom/${yaml_file:?}
else
    print_log "info" "Leaving PT Forward input disabled"
fi

# STDOUT output for debugging all log traffic
if [[ "${ENABLE_STDOUT_OUTPUT,,}" =~ ^(t|true)$ ]]; then
    print_log "info" "Adding STDOUT output for all logs"
    yaml_file="fluent-bit.debug.output.yaml"
    cat <<EOF >/etc/fluent-bit-custom/${yaml_file:?}
pipeline:
  outputs:
    # Debugging output
    - name: stdout
      match: ${output_tag_match:?}
EOF
    echo
    echo /etc/fluent-bit-custom/${yaml_file:?}
    cat /etc/fluent-bit-custom/${yaml_file:?}
else
    print_log "info" "Leaving STDOUT output for all logs disabled"
fi

# Grafana Loki output
if [[ "${ENABLE_GRAFANA_LOKI_OUTPUT,,}" =~ ^(t|true)$ ]]; then
    print_log "info" "Adding Grafana Loki output"
    yaml_file="fluent-bit.grafana-loki.output.yaml"
    cat <<EOF >>/etc/fluent-bit-custom/${yaml_file:?}
pipeline:
  filters:
    # Create a copy of the logs to be formatted before shipping to loki
    - name: rewrite_tag
      match: ${output_tag_match:?}
      rule: \$message .* loki_fmt.\$TAG true
    # Ensure required fields are extracted and formatted for Grafana Loki
    - name: lua
      match: 'loki_fmt.*'
      script: apply-loki-formatting.lua
      call: grafana_loki_formatting

  outputs:
    # Grafana Loki output
    - name: loki
      match: 'loki_fmt.*'
      host: ${GRAFANA_LOKI_HOST:-}
      port: ${GRAFANA_LOKI_PORT:-}
      uri: ${GRAFANA_LOKI_URI:-/loki/api/v1/push}
      tls: off
      labels: input=flb
      label_map_path: /etc/fluent-bit-custom/fluent-bit.grafana-loki.output.logmap.json
      line_format: json
EOF
    sed -i "s/^\(\s*\)#-\( ${yaml_file:?}\)/\1- ${yaml_file:?}/" /etc/fluent-bit-custom/fluent-bit.yaml
    echo
    echo /etc/fluent-bit-custom/${yaml_file:?}
    cat /etc/fluent-bit-custom/${yaml_file:?}
else
    print_log "info" "Leaving Grafana Loki output disabled"
fi

# Upstream Fluentd or Fluent-bit TLS encrypted Forward output
if [[ "${ENABLE_TLS_FORWARD_OUTPUT,,}" =~ ^(t|true)$ ]]; then
    print_log "info" "Adding TLS Forward output"
    yaml_file="fluent-bit.tls-forward.output.yaml"
    cat <<EOF >/etc/fluent-bit-custom/${yaml_file:?}
pipeline:
  outputs:
    # TLS Forward output
    - name: forward
      match: ${output_tag_match:?}
      host: ${TLS_FORWARD_OUTPUT_HOST:?}
      port: ${TLS_FORWARD_OUTPUT_PORT:?}
      shared_key: ${TLS_FORWARD_OUTPUT_SHARED_KEY:?}
      tls: on
      tls.verify: ${TLS_FORWARD_OUTPUT_VERIFY:-off}
EOF
    sed -i "s/^\(\s*\)#-\( ${yaml_file:?}\)/\1- ${yaml_file:?}/" /etc/fluent-bit-custom/fluent-bit.yaml
    echo
    echo /etc/fluent-bit-custom/${yaml_file:?}
    cat /etc/fluent-bit-custom/${yaml_file:?}
else
    print_log "info" "Leaving TLS Forward output disabled"
fi

# Upstream Fluentd or Fluent-bit unencrypted Forward output
if [[ "${ENABLE_PT_FORWARD_OUTPUT,,}" =~ ^(t|true)$ ]]; then
    print_log "info" "Adding PT Forward output"
    yaml_file="fluent-bit.pt-forward.output.yaml"
    cat <<EOF >/etc/fluent-bit-custom/${yaml_file:?}
pipeline:
  outputs:
    # PT Forward output
    - name: forward
      match: ${output_tag_match:?}
      host: ${PT_FORWARD_OUTPUT_HOST:?}
      port: ${PT_FORWARD_OUTPUT_PORT:?}
      tls: off
EOF
    sed -i "s/^\(\s*\)#-\( ${yaml_file:?}\)/\1- ${yaml_file:?}/" /etc/fluent-bit-custom/fluent-bit.yaml
    echo
    echo /etc/fluent-bit-custom/${yaml_file:?}
    cat /etc/fluent-bit-custom/${yaml_file:?}
else
    print_log "info" "Leaving PT Forward output disabled"
fi
echo
echo /etc/fluent-bit-custom/fluent-bit.yaml
cat /etc/fluent-bit-custom/fluent-bit.yaml

# Modify the Lua lib paths or Fluent-bit will not be able to import it
export LUA_PATH="/usr/share/lua/5.1/?.lua;;"
export LUA_CPATH="/usr/lib/$(uname -m)-linux-gnu/lua/5.1/?.so;;"

################################################
# --- Run Fluent-bit
#
print_log "info" "Starting Fluent-Bit"
exec /opt/fluent-bit/bin/fluent-bit -c /etc/fluent-bit-custom/fluent-bit.yaml
