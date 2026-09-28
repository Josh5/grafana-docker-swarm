"""Validate vmagent relay rendering, authentication, forwarding and queue recovery.

Run with --scratch-dir scratch/temp_data from a Cerebro workspace.
Pass --integration to run isolated Docker containers using the pinned images.
Only synthetic metrics and example credentials are used; no live services are changed.
"""

import argparse
import base64
import json
import os
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[1]
TEMPLATE = REPOSITORY / "docker-swarm-templates/docker-compose.vmagent-edge-relay.yml"
OPTIONS = None


def command(arguments, **kwargs):
    return subprocess.run(arguments, check=True, capture_output=True, text=True, **kwargs)


def render(overrides=None):
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("EDGE_VM_", "UPSTREAM_"))}
    environment.update(
        UPSTREAM_REMOTE_WRITE_URL="http://example.invalid/api/v1/write",
    )
    environment.update(overrides or {})
    result = command(
        ["docker", "compose", "-f", str(TEMPLATE), "config", "--format", "json"],
        env=environment,
    )
    service = json.loads(result.stdout)["services"]["vmagent-edge-relay"]
    # Compose escapes dollar signs when serialising a reusable configuration.
    service["environment"] = {key: value.replace("$$", "$") for key, value in service["environment"].items()}
    script = service["entrypoint"][2].replace("$$", "$")
    command(["sh", "-n"], input=script)
    return service, script


class RelayConfigurationTests(unittest.TestCase):
    def generate(self, overrides=None):
        directory = Path(self.directory.name).resolve()
        service, script = render(overrides)
        script = script.replace("/etc/vmagent", str(directory / "runtime"))
        script = script.replace('exec "${@}"', 'printf "%s\\n" "${@}"')
        result = command(["sh", "-c", script], env={**os.environ, **service["environment"]})
        return (
            yaml.safe_load((directory / "runtime/prometheus.yml").read_text()),
            result.stdout.splitlines(),
            service,
        )

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=OPTIONS.scratch_dir)
        self.addCleanup(self.directory.cleanup)

    def test_defaults_keep_collector_endpoint_and_separate_queue(self):
        config, arguments, service = self.generate()
        self.assertEqual(service["ports"][0]["target"], 8429)
        self.assertEqual(service["ports"][0]["published"], "9090")
        self.assertEqual(config["scrape_configs"][0]["job_name"], "vmagent-edge-relay")
        self.assertIn("-remoteWrite.forcePromProto=true", arguments)
        self.assertIn("-remoteWrite.queues=1", arguments)
        self.assertIn("-remoteWrite.tmpDataPath=/vmagent-remotewrite-data", arguments)
        self.assertIn("-remoteWrite.maxDiskUsagePerURL=1GB", arguments)

    def test_swarm_mount_and_private_runtime_directory(self):
        environment = {
            **os.environ,
            "UPSTREAM_REMOTE_WRITE_URL": "http://example.invalid/api/v1/write",
        }
        result = command(["docker", "stack", "config", "-c", str(TEMPLATE)], env=environment)
        service = yaml.safe_load(result.stdout)["services"]["vmagent-edge-relay"]
        self.assertNotIn("tmpfs", service)
        mount = next(item for item in service["volumes"] if item["type"] == "tmpfs")
        self.assertEqual(mount["target"], "/etc/vmagent")
        self.assertEqual(mount["tmpfs"], {"size": 1048576})
        self.assertNotIn("user", service)
        queue_mount = next(item for item in service["volumes"] if item["target"] == "/vmagent-remotewrite-data")
        self.assertEqual(queue_mount["type"], "volume")
        self.assertEqual(queue_mount["source"], "edge-agent-data")
        self.assertEqual(yaml.safe_load(result.stdout)["volumes"]["edge-agent-data"]["name"], "vmagent-edge-relay-data")
        self.generate()
        runtime = Path(self.directory.name) / "runtime"
        self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)

    def test_passwords_and_usernames_keep_shell_punctuation(self):
        password = "example ' password $ with quotes"
        config, arguments, _ = self.generate(
            {
                "EDGE_VM_BASIC_AUTH_USER": "example'user",
                "EDGE_VM_BASIC_AUTH_PASS": password,
                "UPSTREAM_BASIC_AUTH_USER": "example upstream",
                "UPSTREAM_BASIC_AUTH_PASS": password,
            }
        )
        self.assertEqual(config["scrape_configs"][0]["basic_auth"]["username"], "example'user")
        self.assertIn("-httpAuth.username=example'user", arguments)
        self.assertIn("-remoteWrite.basicAuth.username=example upstream", arguments)
        self.assertNotIn(password, arguments)
        self.assertEqual((Path(self.directory.name) / "runtime/receiver-password").read_text(), password)
        self.assertEqual((Path(self.directory.name) / "runtime/upstream-password").stat().st_mode & 0o777, 0o600)

    def test_custom_scrape_relabel_and_unlimited_disk(self):
        scrape = "global:\n  scrape_interval: 30s\nscrape_configs: []\n"
        relabel = "- source_labels: [__name__]\n  regex: unwanted_metric\n  action: drop\n"
        config, arguments, _ = self.generate(
            {
                "EDGE_VM_PROM_SCRAPE_CONFIG": scrape,
                "EDGE_VM_RELABEL_CONFIG": relabel,
                "EDGE_VM_MAX_DISK_USAGE": "0",
            }
        )
        self.assertEqual(config["scrape_configs"], [])
        self.assertEqual((Path(self.directory.name) / "runtime/relabel.yml").read_text(), relabel + "\n")
        self.assertIn("-remoteWrite.maxDiskUsagePerURL=0", arguments)

    def test_partial_auth_configuration_is_rejected(self):
        for key in [
            "EDGE_VM_BASIC_AUTH_USER",
            "EDGE_VM_BASIC_AUTH_PASS",
            "UPSTREAM_BASIC_AUTH_USER",
            "UPSTREAM_BASIC_AUTH_PASS",
        ]:
            with self.subTest(key=key), self.assertRaises(subprocess.CalledProcessError):
                self.generate({key: "example"})

    def test_vmagent_parses_scrape_and_relabel_configuration(self):
        if not OPTIONS.integration:
            self.skipTest("Pass --integration to check with vmagent")
        for overrides in [
            {},
            {"EDGE_VM_RELABEL_CONFIG": "- source_labels: [__name__]\n  regex: unwanted_metric\n  action: drop"},
        ]:
            config, arguments, service = self.generate(overrides)
            directory = Path(self.directory.name).resolve()
            # Use a container path so validation works with the Docker daemon's filesystem.
            arguments = [item.replace(str(directory), "/validation") for item in arguments[1:]]
            arguments = [item for item in arguments if not item.startswith("-remoteWrite.tmpDataPath=")]
            command(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--user",
                    "0",
                    "--mount",
                    f"type=bind,source={directory},target=/validation,readonly",
                    service["image"],
                    *arguments,
                    "-dryRun",
                ]
            )


class RelayIntegrationTests(unittest.TestCase):
    def test_authenticated_forwarding_relabel_and_persistent_queue_recovery(self):
        if not OPTIONS.integration:
            self.skipTest("Pass --integration to run a real relay and receiver")
        import bcrypt

        prefix = f"vmagent-relay-test-{uuid.uuid4().hex[:10]}"
        relay = prefix + "-relay"
        upstream = prefix + "-upstream"
        source = prefix + "-source"
        network = prefix + "-network"
        queue_volume = prefix + "-queue"
        password = "example ' password $ with quotes"
        upstream_password = "example upstream password"
        auth = "Authorization: Basic " + base64.b64encode(f"example'user:{password}".encode()).decode()
        upstream_auth = (
            "Authorization: Basic " + base64.b64encode(f"example-upstream:{upstream_password}".encode()).decode()
        )

        with tempfile.TemporaryDirectory(dir=OPTIONS.scratch_dir) as directory:
            root = Path(directory).resolve()
            root.chmod(0o755)
            (root / "prometheus.yml").write_text("scrape_configs: []\n")
            hashed = bcrypt.hashpw(upstream_password.encode(), bcrypt.gensalt()).decode()
            (root / "web.yml").write_text(f"basic_auth_users:\n  example-upstream: '{hashed}'\n")
            service, script = render(
                {
                    "UPSTREAM_REMOTE_WRITE_URL": f"http://{upstream}:9090/api/v1/write",
                    "EDGE_VM_BASIC_AUTH_USER": "example'user",
                    "EDGE_VM_BASIC_AUTH_PASS": password,
                    "UPSTREAM_BASIC_AUTH_USER": "example-upstream",
                    "UPSTREAM_BASIC_AUTH_PASS": upstream_password,
                    "EDGE_VM_RELABEL_CONFIG": "- source_labels: [__name__]\n"
                    "  regex: relay_unwanted_sample\n  action: drop",
                },
            )
            command(["docker", "network", "create", network])
            try:
                command(
                    [
                        "docker",
                        "run",
                        "-d",
                        "--name",
                        upstream,
                        "--network",
                        network,
                        "--mount",
                        f"type=bind,source={root},target=/validation,readonly",
                        "ghcr.io/prometheus/prometheus:v3.5.4",
                        "--config.file=/validation/prometheus.yml",
                        "--web.config.file=/validation/web.yml",
                        "--web.enable-remote-write-receiver",
                        "--storage.tsdb.path=/prometheus",
                    ]
                )
                arguments = [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    relay,
                    "--network",
                    network,
                    "--memory",
                    "700M",
                    "--mount",
                    "type=tmpfs,target=/etc/vmagent,tmpfs-size=1048576",
                    "--mount",
                    f"type=volume,source={queue_volume},target=/vmagent-remotewrite-data",
                ]
                for key, value in service["environment"].items():
                    arguments.extend(["--env", f"{key}={value}"])
                arguments.extend(["--entrypoint", "/bin/sh", service["image"], "-c", script])
                command(arguments)

                def fetch(url, header=auth):
                    return command(["docker", "exec", relay, "wget", "-qO-", "--header", header, url]).stdout

                def wait_for(predicate):
                    deadline = time.monotonic() + 60
                    while time.monotonic() < deadline:
                        try:
                            if predicate():
                                return
                        except subprocess.CalledProcessError:
                            pass
                        time.sleep(1)
                    self.fail("Relay integration condition did not become true within 60 seconds")

                def query(expression):
                    from urllib.parse import urlencode

                    result = json.loads(
                        fetch(f"http://{upstream}:9090/api/v1/query?" + urlencode({"query": expression}), upstream_auth)
                    )
                    return result["data"]["result"]

                def inject(body):
                    command(["docker", "exec", "-i", relay, "sh", "-c", "cat > /etc/vmagent/sample.txt"], input=body)
                    command(
                        [
                            "docker",
                            "exec",
                            relay,
                            "wget",
                            "-qO-",
                            "--header",
                            auth,
                            "--post-file=/etc/vmagent/sample.txt",
                            "http://127.0.0.1:8429/api/v1/import/prometheus",
                        ]
                    )

                wait_for(lambda: fetch("http://127.0.0.1:8429/health", header="").strip() == "OK")
                self.assertEqual(command(["docker", "exec", relay, "id", "-u"]).stdout.strip(), "0")
                self.assertEqual(
                    command(["docker", "exec", relay, "stat", "-c", "%u:%g %a", "/etc/vmagent"]).stdout.strip(),
                    "0:0 700",
                )
                denied = subprocess.run(
                    ["docker", "exec", relay, "wget", "-qO-", "http://127.0.0.1:8429/metrics"], capture_output=True
                )
                self.assertNotEqual(denied.returncode, 0)
                # A second vmagent verifies the binary remote-write receiver path.
                (root / "source.yml").write_text(
                    "scrape_configs:\n  - job_name: relay-test-source\n"
                    "    scrape_interval: 5s\n    static_configs:\n"
                    "      - targets: ['127.0.0.1:8429']\n"
                )
                (root / "source-password").write_text(password)
                command(
                    [
                        "docker",
                        "run",
                        "-d",
                        "--name",
                        source,
                        "--network",
                        network,
                        "--memory",
                        "128M",
                        "--mount",
                        f"type=bind,source={root},target=/validation,readonly",
                        service["image"],
                        "-promscrape.config=/validation/source.yml",
                        f"-remoteWrite.url=http://{relay}:8429/api/v1/write",
                        "-remoteWrite.forcePromProto=true",
                        "-remoteWrite.queues=1",
                        "-remoteWrite.basicAuth.username=example'user",
                        "-remoteWrite.basicAuth.passwordFile=/validation/source-password",
                    ]
                )
                wait_for(lambda: bool(query('up{job="relay-test-source"} == 1')))
                command(["docker", "stop", source])
                inject('relay_delivery_sample{node_name="example-node"} 7\nrelay_unwanted_sample 9\n')
                wait_for(lambda: bool(query("relay_delivery_sample")))
                self.assertEqual(query("relay_delivery_sample")[0]["value"][1], "7")
                self.assertEqual(query("relay_unwanted_sample"), [])
                wait_for(lambda: any(item["value"][1] == "1" for item in query('up{job="vmagent-edge-relay"}')))

                command(["docker", "stop", upstream])
                inject('relay_buffered_sample{node_name="example-node"} 11\n')
                wait_for(
                    lambda: any(
                        float(line.split()[-1]) > 0
                        for line in fetch("http://127.0.0.1:8429/metrics").splitlines()
                        if line.startswith("vmagent_remotewrite_pending_data_bytes{")
                    )
                )
                command(["docker", "stop", relay])
                command(["docker", "volume", "inspect", queue_volume])
                command(["docker", "start", relay])
                command(["docker", "start", upstream])
                wait_for(lambda: bool(query("relay_buffered_sample")))
                self.assertEqual(query("relay_buffered_sample")[0]["value"][1], "11")
                state = json.loads(command(["docker", "inspect", relay]).stdout)[0]["State"]
                self.assertFalse(state["OOMKilled"])
            except Exception:
                for name in [relay, upstream, source]:
                    logs = subprocess.run(["docker", "logs", "--tail", "20", name], capture_output=True, text=True)
                    print(f"{name}:\n{logs.stdout}{logs.stderr}")
                raise
            finally:
                for name in [relay, upstream, source]:
                    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
                subprocess.run(["docker", "network", "rm", network], capture_output=True)
                subprocess.run(["docker", "volume", "rm", queue_volume], capture_output=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-dir", type=Path, required=True)
    parser.add_argument("--integration", action="store_true")
    OPTIONS, unittest_arguments = parser.parse_known_args()
    OPTIONS.scratch_dir.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[__file__, *unittest_arguments])
