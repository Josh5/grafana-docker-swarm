# Edge relay operation

For initial deployment, follow the [Portainer setup guide](../README.md). Configuration options and defaults are documented in each YAML header and its generated `.env.example`; this page covers tuning, recovery and custom scrape configurations.

## Prometheus edge relay

Deploy `docker-compose.prometheus-edge-relay.yml` with the environment values generated from its header. The relay receives remote-write requests, buffers samples in its agent WAL and forwards them to `UPSTREAM_PROMETHEUS_REMOTE_WRITE_URL`. Keep its data directory on persistent storage.

### Memory and forwarding defaults

The queue defaults suit smaller deployments. Prometheus starts with one shard and can increase to four; it does not allocate the maximum shard count immediately. A full batch sends immediately, while the deadline flushes partial batches during quieter periods.

These defaults permit up to 16,000 samples across the queues and batches, compared with 600,000 using Prometheus 3.5's default maximum of 50 shards, capacity 10,000 and batches of 2,000. This comparison describes queue capacity, not total memory: retained series labels, incoming requests and WAL recovery also consume memory. The Go target encourages garbage collection and leaves headroom, but is not a hard process memory limit; reducing it can increase CPU usage and cannot shrink live series data.

### Sizing for more metrics

Start with the defaults and measure ingestion and successful forwarding. For example, four shards sending full 1,000-sample batches with one-second request latency have a theoretical ceiling of about 4,000 samples/second. This is a sizing estimate, not a benchmark; encoding, network latency, retries, upstream ingestion and CPU limits reduce achievable throughput. Backlog drainage needs spare capacity above the live incoming rate.

For higher throughput, try this profile and measure again:

```env
EDGE_PROM_REMOTE_WRITE_MIN_SHARDS=1
EDGE_PROM_REMOTE_WRITE_MAX_SHARDS=8
EDGE_PROM_REMOTE_WRITE_CAPACITY=6000
EDGE_PROM_REMOTE_WRITE_MAX_SAMPLES_PER_SEND=2000
EDGE_PROM_REMOTE_WRITE_BATCH_SEND_DEADLINE=5s
```

Increase `MAX_SHARDS` gradually when the queue needs more concurrency and the upstream receiver has capacity. Increasing `CAPACITY` buffers transient delays but does not increase receiver throughput. Prometheus 3.5's original queue defaults are `MAX_SHARDS=50`, `CAPACITY=10000` and `MAX_SAMPLES_PER_SEND=2000`; use those values only when measurements justify them and sufficient memory is available. See [Prometheus remote-write tuning](https://prometheus.io/docs/practices/remote_write/).

After metrics return, use `sum(rate(prometheus_remote_storage_samples_total[5m]))` for successfully forwarded samples/second, `prometheus_remote_storage_samples_pending` for samples waiting in queues, and `time() - prometheus_remote_storage_queue_highest_sent_timestamp_seconds` for delivery age. Give each relay unique external labels so its self-scraped metrics remain distinguishable. A growing queue or delivery age requires checking upstream errors and throughput; adding shards does not repair an unavailable upstream.

### Advanced configuration and recovery

`UPSTREAM_PROMETHEUS_REMOTE_WRITE_CONFIG` accepts extra YAML indented four spaces beneath the remote-write item. If it contains an explicit `queue_config`, that entire block replaces the queue variables above, preserving existing custom configurations without duplicate YAML keys. Unspecified fields in that custom block use Prometheus defaults, so include all queue fields needed for the smaller profile. Other settings, such as `write_relabel_configs`, can be supplied alongside the generated queue configuration.

Changing these variables takes effect when Portainer redeploys the updated template. Keep any temporary recovery memory allowance until the relay survives startup and the backlog is draining. Smaller queues limit forwarding buffers; they cannot guarantee that a large existing WAL recovers within the normal memory limit. Agent retention is applied during truncation after startup, so shortening it does not bypass the existing WAL replay and can discard unsent data. Do not delete WAL segments individually.

The `pidof` health check confirms that Prometheus is running, including during WAL replay; it does not establish readiness or successful forwarding. If receiver basic authentication is enabled, the built-in self-scrape also needs authentication through a custom scrape configuration; otherwise its `up` metric reports zero. Credentials must remain in deployment configuration, never in this repository.

## vmagent edge relay

Deploy `docker-compose.vmagent-edge-relay.yml` as an alternative remote-write relay. It receives metrics at `/api/v1/write`, queues unsent data on persistent disk and forwards it to `UPSTREAM_REMOTE_WRITE_URL` using the Prometheus protocol. Central Prometheus remains the metrics store. The template uses the same vmagent version as the node collectors and one sending queue to preserve forwarding order for Prometheus, without reproducing Prometheus's shard and batch options.

VictoriaMetrics recommends `-remoteWrite.queues=1` for receivers that reject out-of-order samples. Multiple queues can send blocks for the same series concurrently. See [vmagent troubleshooting](https://docs.victoriametrics.com/victoriametrics/vmagent/#troubleshooting). This does not repair timestamps already out of order at the relay input or make old samples acceptable to the receiver.

### Optional YAML configuration

Set `EDGE_VM_PROM_SCRAPE_CONFIG` to complete YAML, not a filename or an extra list of jobs. With no value, the relay scrapes itself every 15 seconds using `job="vmagent-edge-relay"`, authenticates automatically when receiver authentication is enabled and uses the container hostname as `instance` to avoid collisions with other relays. To use a stable deployment identity or add targets, supply a full configuration such as:

```yaml
global:
  scrape_interval: 15s
scrape_configs:
  - job_name: vmagent-edge-relay
    static_configs:
      - targets: ["127.0.0.1:8429"]
        labels:
          instance: example-edge
    basic_auth:
      username: example-user
      password_file: /etc/vmagent/receiver-password
```

The `basic_auth` block is needed only when receiver authentication is enabled. Its username must match `EDGE_VM_BASIC_AUTH_USER`; the password file is generated automatically in that case. A custom configuration with `scrape_configs: []` disables scraping while leaving the remote-write receiver active, but also removes self-scraped relay telemetry.

Set `EDGE_VM_RELABEL_CONFIG` to a YAML list using vmagent's remote-write relabel syntax. For example, the following adds a deployment identity while preserving existing metric and node labels:

```yaml
- target_label: edge
  replacement: example-edge
```

Relabel rules affect all received and self-scraped data. Preserve `node_name`, `node_cluster`, the self-scrape job and instance labels when using the supplied delivery alerts. Dropping samples intentionally changes what reaches the central receiver. See [vmagent's receiver and persistence documentation](https://docs.victoriametrics.com/victoriametrics/vmagent/).

### Switching from the Prometheus relay

1. Use the new `vmagent-edge-relay-data` volume and configure the same published port, receiver credentials and upstream destination used by the existing relay.
2. Stop the old **Swarm service**, for example by setting its replicas to zero; stopping its container alone allows Swarm to recreate it. Ensure Portainer GitOps does not restore its replica count during the switch.
3. Deploy the vmagent relay. If the hostname, published port and `/api/v1/write` route stay the same, existing collectors can keep their destination URL. A proxy pointing to the old service name or container port must instead target `vmagent-edge-relay:8429`.
4. Import the updated edge alert group from the metrics delivery alerts linked from the setup guide, then confirm fresh node samples at central Prometheus, successful forwarding and a queue that drains.

The new relay volume starts without the old Prometheus WAL. Redeployments reuse that volume and preserve its queue. Node collectors retain their own queues and may still send older buffered samples; switching the relay does not clear those queues. Upstream outages, authentication errors and receiver rejections can still prevent delivery. Only remove the old relay data once you are satisfied with the switch and have decided to discard those unsent samples.

### Checking delivery

Use `up{job="vmagent-edge-relay"}` to check the default self-scrape, `rate(vmagent_remotewrite_bytes_sent_total{job="vmagent-edge-relay"}[5m])` for successful outgoing bytes and `vmagent_remotewrite_pending_data_bytes{job="vmagent-edge-relay"}` for pending data. Check `vm_persistentqueue_bytes_dropped_total` for disk-limit loss and `vmagent_remotewrite_packets_dropped_total` for rejected upstream blocks. These metrics must reach central Prometheus before Grafana can query them; delivery and Loki alerts cover outages where that telemetry is missing.

The `1GB` default bounds disk usage rather than promising an outage duration. Increase it according to your combined collection rate and desired buffering period. The memory limit is a starting budget, not a guarantee for every deployment; validate throughput and restart behaviour with your actual traffic.
