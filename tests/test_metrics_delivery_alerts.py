"""Validate alert imports and exercise missing-series behaviour with promtool.

Run from the workspace with --scratch-dir scratch/temp_data and, optionally,
--promtool-image ghcr.io/prometheus/prometheus:v3.5.4. No network requests or
Grafana writes are made. Docker is only required for the promtool checks.
"""

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
OPTIONS = None


def load_rules():
    return [
        rule
        for path in sorted((REPOSITORY / "grafana_alert_rules").glob("*.json"))
        for group in json.loads(path.read_text())["groups"]
        for rule in group["rules"]
    ]


class DeliveryAlertTests(unittest.TestCase):
    def test_sync_import_contract(self):
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location(
            "sync_grafana_resources", REPOSITORY / "scripts/sync_grafana_resources.py"
        )
        sync = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sync)
        uids = set()
        for path in sorted((REPOSITORY / "grafana_alert_rules").glob("*.json")):
            group = sync.load_alert_rule_group(path)
            payload = sync.alert_rule_group_payload(group, "example-folder")
            self.assertEqual(payload["interval"], 60)
            for rule in payload["rules"]:
                self.assertNotIn(rule["uid"], uids)
                self.assertLessEqual(len(rule["uid"]), 40)
                uids.add(rule["uid"])
                queries = {query["refId"]: query for query in rule["data"]}
                self.assertEqual(queries[rule["condition"]]["model"]["expression"], "A")
                self.assertTrue(queries["A"]["model"]["instant"])
                self.assertEqual(rule["labels"]["squadcast"], "true")
                self.assertEqual(rule["execErrState"], "Alerting")

    def test_prometheus_missing_and_recovered_series(self):
        if not OPTIONS.promtool_image:
            self.skipTest("Pass --promtool-image to exercise PromQL")
        rules = load_rules()
        by_uid = {rule["uid"]: rule for rule in rules}
        metrics = {
            "node-exporter": "node_time_seconds",
            "cadvisor": "container_last_seen",
            "vmagent": "vm_promscrape_scraped_samples_sum",
        }
        cases = []
        for job, metric in metrics.items():
            expression = by_uid[f"collection-{job}-missing"]["data"][0]["model"]["expr"]
            missing = {"labels": '{node_name="missing-node"}', "value": 0}
            series = [
                {"series": f'up{{job="{job}",node_name="missing-node"}}', "values": "1 stale"},
                {"series": f'up{{job="{job}",node_name="healthy-node"}}', "values": "1+0x1440"},
                {"series": f'{metric}{{job="{job}",node_name="healthy-node"}}', "values": "1+0x1440"},
            ]
            cases.append(
                {
                    "name": f"{job}: one missing node stays visible for a day while another delivers",
                    "interval": "1m",
                    "input_series": series,
                    "promql_expr_test": [{"expr": f"({expression}) < 1", "eval_time": "24h", "exp_samples": [missing]}],
                }
            )
            cases.append(
                {
                    "name": f"{job}: delivery recovery clears the missing node",
                    "interval": "1m",
                    "input_series": [
                        {"series": f'up{{job="{job}",node_name="missing-node"}}', "values": "1 stale"},
                        {
                            "series": f'{metric}{{job="{job}",node_name="missing-node"}}',
                            "values": "_ _ _ _ _ _ _ _ _ _ 1+0x10",
                        },
                    ],
                    "promql_expr_test": [{"expr": f"({expression}) < 1", "eval_time": "15m", "exp_samples": []}],
                }
            )
            cases.append(
                {
                    "name": f"{job}: independent inventory survives expired scrape history",
                    "interval": "1h",
                    "input_series": [
                        {"series": 'node_metrics_expected{node_name="missing-node"}', "values": "1+0x192"},
                        {"series": 'node_metrics_expected{node_name="retired-node"}', "values": "0+0x192"},
                    ],
                    "promql_expr_test": [{"expr": f"({expression}) < 1", "eval_time": "8d", "exp_samples": [missing]}],
                }
            )
        ingestion = by_uid["edge-metrics-ingestion-stopped"]["data"][0]["model"]["expr"]
        cases.append(
            {
                "name": "complete delivery outage returns zero, rather than disappearing",
                "promql_expr_test": [
                    {"expr": ingestion, "eval_time": "10m", "exp_samples": [{"labels": "{}", "value": 0}]}
                ],
            }
        )
        dropped = by_uid["collection-remote-write-data-loss"]["data"][0]["model"]["expr"]
        cases.append(
            {
                "name": "dropped packets are detected even when dropped samples stay zero",
                "interval": "1m",
                "input_series": [
                    {
                        "series": 'vmagent_remotewrite_samples_dropped_total{node_name="example-node"}',
                        "values": "0+0x10",
                    },
                    {
                        "series": 'vmagent_remotewrite_packets_dropped_total{node_name="example-node"}',
                        "values": "0+1x10",
                    },
                ],
                "promql_expr_test": [
                    {
                        "expr": f"({dropped}) > 0",
                        "eval_time": "5m",
                        "exp_samples": [{"labels": '{node_name="example-node"}', "value": 5}],
                    }
                ],
            }
        )
        telemetry = by_uid["edge-metrics-telemetry-missing"]["data"][0]["model"]["expr"]
        for name, series, expected in [
            ("both relay types missing", [], 1),
            (
                "Prometheus relay telemetry present",
                [{"series": "prometheus_remote_storage_queue_highest_sent_timestamp_seconds", "values": "1+0x10"}],
                0,
            ),
            (
                "vmagent relay telemetry present",
                [
                    {
                        "series": 'vmagent_remotewrite_bytes_sent_total{job="vmagent-edge-relay",'
                        'instance="example-edge"}',
                        "values": "1+0x10",
                    }
                ],
                0,
            ),
            (
                "collector telemetry cannot hide a missing relay",
                [
                    {
                        "series": 'vmagent_remotewrite_bytes_sent_total{job="vmagent",instance="example-node"}',
                        "values": "1+0x10",
                    }
                ],
                1,
            ),
        ]:
            cases.append(
                {
                    "name": name,
                    "interval": "1m",
                    "input_series": series,
                    "promql_expr_test": [
                        {"expr": telemetry, "eval_time": "10m", "exp_samples": [{"labels": "{}", "value": expected}]}
                    ],
                }
            )
        relay_dropped = by_uid["edge-vmagent-data-loss"]["data"][0]["model"]["expr"]
        cases.append(
            {
                "name": "vmagent disk queue loss detected without rejected upstream packets",
                "interval": "1m",
                "input_series": [
                    {
                        "series": 'vmagent_remotewrite_packets_dropped_total{job="vmagent-edge-relay",'
                        'instance="example-edge"}',
                        "values": "0+0x10",
                    },
                    {
                        "series": 'vm_persistentqueue_bytes_dropped_total{job="vmagent-edge-relay",'
                        'instance="example-edge"}',
                        "values": "0+100x10",
                    },
                ],
                "promql_expr_test": [
                    {
                        "expr": f"({relay_dropped}) > 0",
                        "eval_time": "5m",
                        "exp_samples": [{"labels": '{instance="example-edge"}', "value": 500}],
                    }
                ],
            }
        )
        prometheus_rules = []
        for rule in rules:
            if rule["data"][0]["datasourceUid"] != "prometheus":
                continue
            evaluator = rule["data"][1]["model"]["conditions"][0]["evaluator"]
            operator = ">" if evaluator["type"] == "gt" else "<"
            expression = rule["data"][0]["model"]["expr"]
            prometheus_rules.append(
                {
                    "alert": rule["uid"],
                    "expr": f"({expression}) {operator} {evaluator['params'][0]}",
                    "for": rule["for"],
                }
            )
        with tempfile.TemporaryDirectory(dir=OPTIONS.scratch_dir) as directory:
            root = Path(directory).resolve()
            # JSON is valid YAML and avoids an extra parser dependency.
            (root / "rules.json").write_text(json.dumps({"groups": [{"name": "delivery", "rules": prometheus_rules}]}))
            (root / "cases.json").write_text(
                json.dumps({"rule_files": [], "evaluation_interval": "1m", "tests": cases})
            )
            for arguments in [
                ["check", "rules", "/validation/rules.json"],
                ["test", "rules", "/validation/cases.json"],
            ]:
                result = subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--user",
                        "0",
                        "--entrypoint",
                        "/bin/promtool",
                        "--mount",
                        f"type=bind,source={root},target=/validation,readonly",
                        OPTIONS.promtool_image,
                        *arguments,
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-dir", type=Path, required=True)
    parser.add_argument("--promtool-image")
    OPTIONS, unittest_arguments = parser.parse_known_args()
    OPTIONS.scratch_dir.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[__file__, *unittest_arguments])
