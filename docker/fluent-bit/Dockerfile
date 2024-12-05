FROM ubuntu:latest

RUN \
    echo "**** update apt repositories ****" \
        && apt-get update \
    && \
    echo "**** install container runtime packages ****" \
        && apt-get install -y --no-install-recommends \
            bash \
            ca-certificates \
            certbot \
            curl \
            gnupg \
            tini \
    && \
    echo "**** install wait-for-it script ****" \
        && curl https://raw.githubusercontent.com/vishnubob/wait-for-it/81b1373f17855a4dc21156cfe1694c31d7d1792e/wait-for-it.sh \
            -o /usr/bin/wait-for-it \
        && chmod +x /usr/bin/wait-for-it \
    && \
    echo "**** install Fluent-bit ****" \
        && curl https://raw.githubusercontent.com/fluent/fluent-bit/master/install.sh \
            -o /tmp/install.sh \
        && chmod +x /tmp/install.sh \
        && /tmp/install.sh \
        && rm -f /tmp/install.sh \
    && \
    echo "**** install lua dependencies ****" \
        && apt-get install -y --no-install-recommends \
            lua-cjson \
    && \
    echo "**** cleanup apt cache ****" \
        && apt-get clean \
        && rm -rf /var/lib/apt/lists/* \
    && \
    echo

COPY overlay/ /

ENV \
    FLUENT_BIT_LOG_LEVEL=info \
    FLUENT_BIT_TAG_PREFIX=flb_glf. \
    HOST_HOSTNAME=grafana-fluent-bit \
    FORWARD_SHARED_KEY=1234567890 \
    FLUENT_STORAGE_PATH=/var/fluent-bit/storage \
    ENABLE_FORWARD_TLS=true \
    CERTIFICATES_DIRECTORY=/etc/fluent-bit/certs \
    USE_EXISTING_CERT=false \
    EXISTING_KEY_PATH=/etc/fluent-bit/certs/1.key \
    EXISTING_CERT_PATH=/etc/fluent-bit/certs/1.pem \
    USE_CERTBOT_TO_GENERATE_KEY=false \
    CERT_FQDN=example.com \
    CERT_EMAIL=admin@example.com \
    ENABLE_STDOUT_OUTPUT=false \
    ENABLE_GRAFANA_LOKI_OUTPUT=false \
    GRAFANA_LOKI_HOST=loki \
    GRAFANA_LOKI_PORT=3100 \
    GRAFANA_LOKI_URI=/logs/loki/api/v1/push \
    ENABLE_TLS_FORWARD_OUTPUT=false \
    ENABLE_PT_FORWARD_OUTPUT=false

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
CMD []
