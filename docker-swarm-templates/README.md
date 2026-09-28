# Docker Swarm Stack Setup

## Choose a template

| Template                                                                                               | Purpose                                                 |
| :----------------------------------------------------------------------------------------------------- | :------------------------------------------------------ |
| [docker-compose.grafana-stack.yml](./docker-compose.grafana-stack.yml)                                 | Central Grafana, Prometheus and Loki services.          |
| [docker-compose.minio.yml](./docker-compose.minio.yml)                                                 | MinIO object storage for Loki.                          |
| [docker-compose.node-metrics-collection-stack.yml](./docker-compose.node-metrics-collection-stack.yml) | Collect host and container metrics on each node.        |
| [docker-compose.vmagent-edge-relay.yml](./docker-compose.vmagent-edge-relay.yml)                       | Receive and forward metrics through a vmagent relay.    |
| [docker-compose.prometheus-edge-relay.yml](./docker-compose.prometheus-edge-relay.yml)                 | Receive and forward metrics through a Prometheus relay. |

Deploy the central stack first, then configure collectors to send metrics directly to its receiver or through one of the edge relay templates. If Loki uses MinIO, prepare its bucket and credentials using the [MinIO setup guide](./docs/minio.md).

## Deploy with Portainer

1. Prepare the external networks and host directories required by the chosen YAML, following its setup instructions.
2. In your Swarm environment, add a stack and select **Repository** as the build method. Name it after the chosen template, for example `vmagent-edge-relay`.
3. Set **Repository URL** to `<url>` and **Repository reference** to `refs/heads/<branch>`.
4. Set **Compose path** to the YAML filename, for example `docker-compose.vmagent-edge-relay.yml`.
5. Open the matching `.env.example` alongside that YAML on the release branch. For example, use [docker-compose.vmagent-edge-relay.env.example](<url>/blob/<branch>/docker-compose.vmagent-edge-relay.env.example). Copy its contents into **Environment variables → Advanced mode**, then enter your deployment values. Option descriptions and defaults come from the YAML header.
6. Enable GitOps polling updates with an interval of `5m`, then deploy the stack.

The publish action generates `<YAML filename without .yml>.env.example` from the header between `<config_start>` and `<config_end>`. These files are published on `<branch>`; they are generated artifacts and need not exist in the source directory.

![Portainer Grafana Stack Creation](./docs/images/portainer-grafana-stack-create.png)

## Edge relay setup

For vmagent, Docker creates the `vmagent-edge-relay-data` queue volume automatically. Pin placement to the node holding that volume; no `EDGE_VM_DATA_PATH` or host ownership setup is required. The default configuration scrapes the relay itself.

When replacing a relay, stop the old Swarm service before reusing its published port. Keep the collector destination and receiver credentials consistent, and update any proxy to the new service and internal port.

See [edge relay operation](./docs/edge-relays.md) for tuning, recovery and custom scrape configurations, and [metrics delivery alerts](<url>/blob/master/grafana_alert_rules/README.md) to monitor ingestion and forwarding.
