"""Validate the rendered relay startup configuration without starting a relay.

Run from the Cerebro workspace with its Python environment and pass
--scratch-dir scratch/temp_data. Docker Compose and PyYAML are required.
Pass --promtool-image to also check each configuration with Prometheus's parser.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


REPOSITORY = Path(__file__).resolve().parents[1]
TEMPLATE = REPOSITORY / "docker-swarm-templates/docker-compose.prometheus-edge-relay.yml"
OPTIONS = None


class RelayConfigurationTests(unittest.TestCase):
    def render(self, overrides=None):
        with tempfile.TemporaryDirectory(dir=OPTIONS.scratch_dir) as directory:
            config_directory = Path(directory).resolve()
            environment = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("EDGE_PROM_", "UPSTREAM_PROMETHEUS_"))
            }
            environment.update(
                EDGE_PROM_DATA_PATH=str(config_directory),
                UPSTREAM_PROMETHEUS_REMOTE_WRITE_URL="http://example.invalid/api/v1/write",
            )
            environment.update(overrides or {})
            compose = subprocess.run(
                ["docker", "compose", "-f", str(TEMPLATE), "config", "--format", "json"],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            service = json.loads(compose.stdout)["services"]["prometheus-edge-relay"]
            script = service["entrypoint"][2].replace("$$", "$")
            subprocess.run(["sh", "-n"], input=script, text=True, check=True)
            script = script.replace("/etc/prometheus", str(config_directory))
            script = script.replace("exec /bin/prometheus", "printf '%s\\n'")
            generated = subprocess.run(
                ["sh", "-c", script],
                env={**environment, **service["environment"]},
                check=True,
                capture_output=True,
                text=True,
            )
            config_path = config_directory / "prometheus.yml"
            config_text = config_path.read_text()
            config = yaml.safe_load(config_text)
            if OPTIONS.promtool_image:
                subprocess.run(
                    [
                        "docker", "run", "--rm", "--network", "none", "--user", "0",
                        "--entrypoint", "/bin/promtool", "--mount",
                        f"type=bind,source={config_directory},target=/validation,readonly",
                        OPTIONS.promtool_image, "check", "config", "--agent",
                        "/validation/prometheus.yml",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            return config, config_text, generated.stdout.splitlines(), service

    def test_small_defaults(self):
        config, _, arguments, _ = self.render()
        self.assertEqual(config["remote_write"][0]["queue_config"], {
            "min_shards": 1,
            "max_shards": 4,
            "capacity": 3000,
            "max_samples_per_send": 1000,
            "batch_send_deadline": "5s",
        })
        self.assertIn("--auto-gomemlimit.ratio=0.70", arguments)
        self.assertIn("--storage.agent.retention.max-time=6h", arguments)

    def test_custom_queue_memory_and_retention(self):
        config, _, arguments, service = self.render({
            "EDGE_PROM_REMOTE_WRITE_MIN_SHARDS": "2",
            "EDGE_PROM_REMOTE_WRITE_MAX_SHARDS": "8",
            "EDGE_PROM_REMOTE_WRITE_CAPACITY": "6000",
            "EDGE_PROM_REMOTE_WRITE_MAX_SAMPLES_PER_SEND": "2000",
            "EDGE_PROM_REMOTE_WRITE_BATCH_SEND_DEADLINE": "10s",
            "EDGE_PROM_GOMEMLIMIT_RATIO": "0.65",
            "EDGE_PROM_RETENTION_TIME": "3h",
        })
        self.assertEqual(config["remote_write"][0]["queue_config"], {
            "min_shards": 2, "max_shards": 8, "capacity": 6000,
            "max_samples_per_send": 2000, "batch_send_deadline": "10s",
        })
        self.assertEqual(service["environment"]["EDGE_PROM_RETENTION_TIME"], "3h")
        self.assertIn("--auto-gomemlimit.ratio=0.65", arguments)
        self.assertIn("--storage.agent.retention.max-time=3h", arguments)

    def test_legacy_queue_override(self):
        config, text, _, _ = self.render({
            "UPSTREAM_PROMETHEUS_REMOTE_WRITE_CONFIG":
                "    queue_config:\n      max_shards: 2\n      capacity: 6000\n"
                "    write_relabel_configs:\n      - source_labels: [__name__]\n"
                "        regex: unwanted_metric\n        action: drop",
        })
        self.assertEqual(text.count("queue_config:"), 1)
        self.assertEqual(config["remote_write"][0]["queue_config"], {
            "max_shards": 2, "capacity": 6000,
        })
        self.assertEqual(config["remote_write"][0]["write_relabel_configs"][0]["action"], "drop")

    def test_extra_config_and_auth_preserved(self):
        config, _, _, _ = self.render({
            "UPSTREAM_PROMETHEUS_BASIC_AUTH_USER": "example-user",
            "UPSTREAM_PROMETHEUS_BASIC_AUTH_PASS": "example-password",
            "UPSTREAM_PROMETHEUS_REMOTE_WRITE_CONFIG": "    remote_timeout: 10s",
        })
        remote_write = config["remote_write"][0]
        self.assertEqual(remote_write["remote_timeout"], "10s")
        self.assertEqual(remote_write["basic_auth"]["username"], "example-user")
        self.assertEqual(remote_write["queue_config"]["max_shards"], 4)

    def test_no_upstream(self):
        config, _, _, _ = self.render({"UPSTREAM_PROMETHEUS_REMOTE_WRITE_URL": ""})
        self.assertNotIn("remote_write", config)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-dir", type=Path, required=True)
    parser.add_argument("--promtool-image")
    OPTIONS, unittest_arguments = parser.parse_known_args()
    OPTIONS.scratch_dir.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[__file__, *unittest_arguments])
