# Metrics delivery alerts

These Grafana-managed rules check node metrics at the final Prometheus receiver and delivery errors in Loki. Each JSON file contains one group for `scripts/sync_grafana_resources.py`. Both groups evaluate every minute in the `Infrastructure` folder and use datasource UIDs `prometheus` and `loki`. Change those values for another Grafana installation.

| Group                                  | Checks                                                                                                                                                                                        |
| :------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Metrics Delivery - Edge Relay`        | No fresh node metrics at the receiver, missing relay forwarding telemetry, Prometheus delivery lag, vmagent backlog and data loss, upstream failures, rejected samples and repeated startups. |
| `Metrics Delivery - Collection Stacks` | Missing node-exporter, cAdvisor or vmagent metrics per node, failed scrape targets, a persistent disk queue, dropped samples or blocks, delivery errors and application errors in logs.       |

## Missing metrics and expected nodes

The collection templates label metrics with `node_name` and use the jobs `node-exporter`, `cadvisor` and `vmagent`. Delivery checks look for actual metric samples from each job during the last five minutes, then require the missing condition to persist for another five minutes. An exporter that reports scrape status but returns no expected metrics also triggers the delivery check. Successful collection at the source does not satisfy these checks until the samples reach the final receiver.

Nodes are discovered from seven days of `up` history. A zero-valued fallback keeps a missing node visible while other nodes continue delivering, instead of allowing Grafana to evict the missing series immediately. Historical discovery requires seven days of Prometheus retention and can forget a missing node when its last historical sample expires. Newly deployed nodes are discovered after their first scrape status reaches the receiver; nodes that have never delivered are not known automatically.

For durable expected-node coverage, expose `node_metrics_expected{node_name="example-node"} 1` through a centrally scraped target independent of the collection and relay path, such as a receiver-side node-exporter textfile collector. Publish one series per expected node. This optional inventory is supported by all three delivery checks and keeps a missing node visible beyond the history window, including nodes that have never delivered. Remove an inventory series or set it to zero when retiring a node; historical discovery can still retain it for seven days, so also adjust the selectors when intentionally excluding a retired node.

The distributed JSON contains no host inventory or environment exclusions. Before importing, narrow the metric selectors with your deployment's `node_name` or `node_cluster` labels and the log selectors with `source_hostname`, `source_env_type` or other established labels if required. Apply the same scope to the live metric, historical discovery and optional inventory selectors. The complete-ingestion check should use the same node scope. The alert annotations preserve node labels for notification routing and silences.

## Thresholds and logging

| Check                                      | Window and threshold                                                            | Pending period |
| :----------------------------------------- | :------------------------------------------------------------------------------ | :------------- |
| Missing node metrics or all node ingestion | No fresh samples in five minutes                                                | Five minutes   |
| Missing relay telemetry                    | Neither Prometheus highest-sent nor vmagent sent-byte telemetry in five minutes | Five minutes   |
| Prometheus relay delivery lag              | Highest successfully sent timestamp more than 300 seconds old                   | Five minutes   |
| vmagent relay queue                        | Any destination has more than 100 MiB pending                                   | Ten minutes    |
| vmagent relay data loss                    | Rejected-block or discarded-disk-byte counter increased in ten minutes          | Immediate      |
| Collector queue                            | Any destination has more than 100 MiB pending                                   | Ten minutes    |
| Dropped collector samples or blocks        | Counter increased during ten minutes                                            | Immediate      |
| Repeated delivery or application errors    | At least three matching lines during ten minutes                                | Two minutes    |
| Rejected relay samples                     | At least one matching line during ten minutes                                   | Immediate      |
| Relay restarts                             | At least two startup messages during fifteen minutes                            | Immediate      |

The log checks match service names from the supplied Swarm and standalone collection templates, plus unprefixed service names. Edge checks recognise both `prometheus-edge-relay` and `vmagent-edge-relay`, including their default Swarm stack prefixes. Adjust them if your stack names differ. Application text is inspected rather than relying on the indexed `level`, because a logging pipeline may label application errors as `info`. The restart rule counts `Starting Prometheus Agent`, `Starting Prometheus Server` or `starting vmagent at`; individual WAL segment and persistent-queue messages do not trigger it.

Loki checks remain available when metrics forwarding fails, provided log forwarding still works. No matching error logs is normal and returns zero. Supporting backlog, drop, target-down and lag checks treat missing data as OK because dedicated delivery checks cover missing telemetry. All rules alert on query errors rather than silently retaining a healthy state. These rules cannot report through Grafana when Grafana itself is unavailable; monitor that availability independently.

Relay telemetry checks require its self-scrape to work and be forwarded. Prometheus relays use highest-sent timestamps; vmagent relays use `vmagent_remotewrite_bytes_sent_total{job="vmagent-edge-relay"}` for telemetry presence, pending bytes for backlog and counters for rejected blocks or disk-limit drops. Telemetry presence establishes delivery of self-metrics, not that every buffered sample was delivered. The timestamp lag rule is specific to Prometheus; end-to-end node-delivery checks and queue checks also cover vmagent. The complete-telemetry-loss rule accepts either relay type, so installations running multiple relays should scope it per deployment or add per-relay expected inventory checks.

Set unique external labels on Prometheus relays. The vmagent template uses the container hostname as the self-scrape `instance` by default; a custom scrape configuration can provide a stable deployment identity. Retain `job="vmagent-edge-relay"` when customising that configuration. Receiver authentication is included in vmagent's default self-scrape; custom scrape configurations need their own authentication. See the [relay operation guide](../docker-swarm-templates/docs/edge-relays.md). The missing-telemetry rule intentionally alerts if telemetry is unavailable even while node metrics are flowing. Reimport `edge-relay.json` after switching relay types; the two alert groups remain separate.

Rules carry `squadcast="true"`, matching the existing notification-routing convention, plus `severity` and `component`. Configure notification policies/contact points for those labels before importing; rule creation alone does not establish a working notification destination. Use silences for planned redeployments or backlog recovery, and inspect rejected samples before assuming recovery preserved every sample.

## Import

From the project root, provide `GRAFANA_URL` and `GRAFANA_AUTH` through your local environment. Preview each group first; the script resolves `Infrastructure` to its folder UID, or accepts `--folder-uid` for another folder. The preview does not create the folder or change Grafana.

```sh
python3 scripts/sync_grafana_resources.py import-alert-rules grafana_alert_rules/edge-relay.json --overwrite --dry-run
python3 scripts/sync_grafana_resources.py import-alert-rules grafana_alert_rules/collection-stacks.json --overwrite --dry-run
```

Remove `--dry-run` to publish the reviewed groups. `--overwrite` replaces the complete group with the file's rules, so keep any desired live changes in the JSON before publishing.

## Validation

The tests verify compatibility with the sync script and can run Prometheus's parser and expression tests without writing to Grafana. They exercise partial outages, recovery, inventory coverage after history expires, complete delivery loss and dropped blocks when the dropped-sample counter remains zero.

```sh
python3 tests/test_metrics_delivery_alerts.py --scratch-dir /tmp/metrics-alert-validation --promtool-image ghcr.io/prometheus/prometheus:v3.5.4
```

In a Cerebro workspace, pass its `scratch/temp_data` directory instead. Live PromQL and LogQL checks can additionally confirm your datasource labels and service-name conventions before import. See [Grafana's missing-data guidance](https://grafana.com/docs/grafana/latest/alerting/guides/missing-data/) for the distinction between an empty query and one missing alert instance.
