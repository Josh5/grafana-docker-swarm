"""Exercise Loki template publication without contacting object storage.

Run with --scratch-dir scratch/temp_data using Python with PyYAML and Docker CLI.
Pass --loki-image grafana/loki:3.7.9 to additionally use Loki's config validator.
Only synthetic credentials are used; no live services are changed.
"""

import argparse
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[1]
TEMPLATE = REPOSITORY / "docker-swarm-templates/docker-compose.grafana-stack.yml"
OPTIONS = None


def render(overrides=None, stack=False):
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": os.environ["HOME"],
        "TRAEFIK_DOMAIN": "example.invalid",
        "TRAEFIK_PATH_PREFIX_PROMETHEUS": "/prom",
        "TRAEFIK_PATH_PREFIX_LOKI": "/logs",
        "LOKI_PLACEMENT_CONSTRAINT": "node.hostname==monitor-1",
        "GRAFANA_DATA_PATH": "/validation/grafana",
        "PROMETHEUS_DATA_PATH": "/validation/prometheus",
        "ALERTMANAGER_DATA_PATH": "/validation/alertmanager",
        "LOKI_DATA_PATH": "/validation/loki",
        "LOKI_USERNAME": "example",
        "LOKI_PASSWORD_HASH": "example-hash",
        "PROMETHEUS_BASIC_AUTH_PASS_HASH": "example-hash",
        "AWS_REGION": "eu-west-1",
        "LOKI_S3_BUCKET_NAME": "example-bucket",
        "LOKI_S3_ENDPOINT": "minio:9000",
        "ACCESS_KEY_ID": "example-access-key",
        "SECRET_ACCESS_KEY": "example-secret/+with=symbols",
        "LOKI_AZURE_STORAGE_ACCOUNT_NAME": "exampleaccount",
        "LOKI_AZURE_STORAGE_ACCOUNT_KEY": "ZXhhbXBsZQ==",
    }
    environment.update(overrides or {})
    if stack:
        arguments = ["docker", "stack", "config", "-c", str(TEMPLATE)]
    else:
        arguments = [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "-f",
            str(TEMPLATE),
            "config",
            "--format",
            "json",
        ]
    result = subprocess.run(arguments, env=environment, capture_output=True, text=True, check=True)
    model = yaml.safe_load(result.stdout) if stack else json.loads(result.stdout)
    return model


def expand_environment(text, environment):
    """Expand the placeholder forms used here for YAML assertions, not Loki validation."""
    return re.sub(
        r"\$\{([A-Z_][A-Z_0-9]*)(?::-([^}]*))?\}",
        lambda match: environment.get(match[1]) or match[2] or "",
        text,
    )


class LokiGatewayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=OPTIONS.scratch_dir)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def generate(self, model, full_startup=False, script_transform=None):
        gateway = model["services"]["loki-gateway"]
        script = gateway["entrypoint"][2].replace("$$", "$")
        subprocess.run(["sh", "-n"], input=script, text=True, check=True)
        if not full_startup:
            script = script.split("\nwait_for_service()", 1)[0]
        script = script.replace("/etc/loki-generated", str(self.root))
        if script_transform:
            script = script_transform(script)
        environment = {"PATH": os.environ["PATH"], **gateway["environment"]}
        environment["LOKI_CONFIG_FILE"] = str(self.root / Path(environment["LOKI_CONFIG_FILE"]).name)
        result = subprocess.run(["sh", "-c", script], env=environment, capture_output=True, text=True)
        return result, Path(environment["LOKI_CONFIG_FILE"])

    def test_backends_keep_secrets_and_settings_for_loki(self):
        for backend, minio in [("s3", "false"), ("s3", "true"), ("azure", "false")]:
            with self.subTest(backend=backend, minio=minio):
                model = render(
                    {
                        "LOKI_OBJECT_STORE": backend,
                        "LOKI_S3_USE_MINIO": minio,
                        "LOKI_RETENTION_PERIOD": "30d",
                        "LOKI_MAX_CHUNK_AGE": "4h",
                        "LOKI_GRPC_SERVER_MAX_RECV_MSG_SIZE": "41943040",
                    }
                )
                result, path = self.generate(model)
                self.assertEqual(result.returncode, 0, result.stderr)
                text = path.read_text()
                loki = model["services"]["loki-mono"]
                environment = loki["environment"]
                for key in ("ACCESS_KEY_ID", "SECRET_ACCESS_KEY", "LOKI_AZURE_STORAGE_ACCOUNT_KEY"):
                    self.assertNotIn(key, model["services"]["loki-gateway"]["environment"])
                    self.assertNotIn(environment[key], text + result.stdout + result.stderr)
                config = yaml.safe_load(expand_environment(text, environment))
                self.assertNotIn("ingester_rf1", config)
                self.assertEqual(config["limits_config"]["retention_period"], "30d")
                self.assertEqual(config["ingester"]["max_chunk_age"], "4h")
                self.assertEqual(config["querier"]["query_ingesters_within"], "4h")
                self.assertEqual(config["server"]["grpc_server_max_recv_msg_size"], 41943040)
                schema = config["schema_config"]["configs"][0]
                self.assertEqual((schema["store"], schema["schema"], schema["object_store"]), ("tsdb", "v13", backend))
                if backend == "s3":
                    storage = config["storage_config"]["aws"]
                    self.assertEqual(storage["s3forcepathstyle"], minio == "true")
                    self.assertEqual(storage["secret_access_key"], environment["SECRET_ACCESS_KEY"])
                    self.assertNotIn("s3", storage)
                    if minio == "true":
                        self.assertEqual(storage["endpoint"], "minio:9000")
                        self.assertTrue(storage["insecure"])
                else:
                    self.assertEqual(
                        config["storage_config"]["azure"]["account_key"], environment["LOKI_AZURE_STORAGE_ACCOUNT_KEY"]
                    )
                self.assertEqual(path.stat().st_mode & 0o777, 0o444)
                if OPTIONS.loki_image:
                    environment_arguments = []
                    for key, value in environment.items():
                        environment_arguments.extend(["--env", f"{key}={value}"])
                    subprocess.run(
                        [
                            "docker",
                            "run",
                            "--rm",
                            "--network",
                            "none",
                            "--user",
                            str(os.getuid()),
                            *environment_arguments,
                            "--mount",
                            f"type=bind,source={self.root},target=/validation,readonly",
                            OPTIONS.loki_image,
                            f"-config.file=/validation/{path.name}",
                            "-config.expand-env=true",
                            "-verify-config=true",
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                    )

    def test_environment_changes_reuse_the_template_and_aws_supports_iam(self):
        model = render({"ACCESS_KEY_ID": "", "SECRET_ACCESS_KEY": ""})
        result, path = self.generate(model)
        self.assertEqual(result.returncode, 0, result.stderr)
        original = path.read_bytes()
        environment = model["services"]["loki-mono"]["environment"]
        config = yaml.safe_load(expand_environment(path.read_text(), environment))
        self.assertEqual(config["storage_config"]["aws"]["access_key_id"], "")
        self.assertEqual(config["storage_config"]["aws"]["secret_access_key"], "")
        changed = render({"LOKI_RETENTION_PERIOD": "60d", "SECRET_ACCESS_KEY": "changed-example"})
        result, changed_path = self.generate(changed)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(changed_path, path)
        self.assertEqual(changed_path.read_bytes(), original)
        config = yaml.safe_load(expand_environment(path.read_text(), changed["services"]["loki-mono"]["environment"]))
        self.assertEqual(config["limits_config"]["retention_period"], "60d")
        self.assertEqual(config["storage_config"]["aws"]["secret_access_key"], "changed-example")

    def test_swarm_keeps_placeholders_literal_at_gateway_startup(self):
        model = render({"LOKI_OBJECT_STORE": "azure"}, stack=True)
        result, path = self.generate(model)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("${LOKI_AZURE_STORAGE_ACCOUNT_KEY}", path.read_text())
        self.assertIn("${RETENTION_PERIOD:-90d}", path.read_text())
        self.assertNotIn("ZXhhbXBsZQ==", path.read_text() + result.stdout + result.stderr)

    def test_publication_is_atomic_and_restarts_preserve_the_file(self):
        model = render()
        result, path = self.generate(model)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = path.stat()
        text = path.read_text()
        result, _ = self.generate(model)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.stat().st_ino, before.st_ino)
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(path.read_text(), text)
        self.assertEqual(list(self.root.glob(".loki.*")), [])
        result, _ = self.generate(model, script_transform=lambda script: script.replace("chmod 0444", "false #"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_text(), text)
        self.assertEqual(list(self.root.glob(".loki.*")), [])
        path.unlink()
        result, _ = self.generate(model, script_transform=lambda script: script.replace("chmod 0444", "false #"))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(path.exists())
        self.assertEqual(list(self.root.glob(".loki.*")), [])

    def test_changed_template_requires_a_new_revision(self):
        model = render()
        result, path = self.generate(model)
        self.assertEqual(result.returncode, 0, result.stderr)
        original = path.read_bytes()
        result, _ = self.generate(
            model, script_transform=lambda script: script.replace("max_concurrent: 4", "max_concurrent: 5")
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("revision bump", result.stderr)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".loki.*")), [])

    def test_invalid_selectors_publish_nothing(self):
        for overrides in [{"LOKI_OBJECT_STORE": "filesystem"}, {"LOKI_S3_USE_MINIO": "yes"}]:
            with self.subTest(overrides=overrides):
                result, path = self.generate(render(overrides))
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(path.exists())
                self.assertEqual(list(self.root.glob(".loki.*")), [])

    def test_compose_and_swarm_agree_on_mounts_path_and_placement(self):
        paths = set()
        for overrides in [{}, {"LOKI_S3_USE_MINIO": "true"}, {"LOKI_OBJECT_STORE": "azure"}]:
            for stack in (False, True):
                model = render(overrides, stack=stack)
                loki = model["services"]["loki-mono"]
                gateway = model["services"]["loki-gateway"]
                path = gateway["environment"]["LOKI_CONFIG_FILE"]
                self.assertEqual(loki["command"][1], path)
                paths.add(path)
                self.assertEqual(loki["image"], "grafana/loki:3.7.9")
                self.assertFalse(loki.get("entrypoint"))
                self.assertIn("-config.expand-env=true", loki["command"])
                self.assertNotIn("configs", model)
                self.assertNotIn("secrets", model)
                self.assertEqual(loki["deploy"]["placement"], gateway["deploy"]["placement"])
                self.assertIn("node.hostname==monitor-1", loki["deploy"]["placement"]["constraints"])
                mounts = [mount for mount in loki["volumes"] if mount["target"] == "/etc/loki-generated"]
                self.assertTrue(mounts[0]["read_only"])
                self.assertEqual(mounts[0]["source"], gateway["volumes"][0]["source"])
        self.assertEqual(len(paths), 3)

    def test_gateway_publishes_before_readiness_and_executes_nginx(self):
        model = render()
        commands = self.root / "bin"
        commands.mkdir()
        (commands / "curl").write_text(
            '#!/bin/sh\nset -eu\ntest -s "$LOKI_CONFIG_FILE"\n'
            'if [ ! -e "$LOKI_CONFIG_FILE.checked" ]; then\n'
            '  touch "$LOKI_CONFIG_FILE.checked"\n  exit 1\nfi\n'
        )
        (commands / "sleep").write_text("#!/bin/sh\nexit 0\n")
        for command in commands.iterdir():
            command.chmod(0o755)
        gateway = model["services"]["loki-gateway"]
        gateway["environment"]["PATH"] = f"{commands}:{os.environ['PATH']}"
        result, path = self.generate(
            model,
            full_startup=True,
            script_transform=lambda script: script.replace(
                "/etc/nginx/nginx.conf", str(self.root / "nginx.conf")
            ).replace('exec /docker-entrypoint.sh nginx -g "daemon off;"', 'exec printf "nginx-started\\n"'),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(path.exists())
        self.assertTrue(Path(f"{path}.checked").exists())
        self.assertLess(result.stdout.index("template is ready"), result.stdout.index("Waiting for"))
        self.assertTrue(result.stdout.endswith("nginx-started\n"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-dir", type=Path, required=True)
    parser.add_argument("--loki-image")
    OPTIONS, arguments = parser.parse_known_args()
    OPTIONS.scratch_dir.mkdir(parents=True, exist_ok=True)
    unittest.main(argv=[__file__, *arguments])
