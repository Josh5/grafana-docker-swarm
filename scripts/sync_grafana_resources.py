"""
Usage:
    python3 scripts/sync_grafana_resources.py [--url URL] [--auth AUTH] <command> [options]

Description:
    Export and import Grafana-managed alert rules and dashboards through the
    Grafana HTTP API. Resources are written as formatted JSON files.

Arguments:
    --url: Grafana base URL; defaults to GRAFANA_URL.
    --auth: Grafana API key, service-account token, or username:password;
        defaults to GRAFANA_AUTH. Tokens use Bearer authentication and
        username:password uses Basic authentication.
    <command>: One of export-alert-rules, import-alert-rules,
        export-dashboards, or import-dashboards.
    --uid/--output-file: Select one dashboard and its export filename.
    --for-sharing-externally: Export with portable datasource inputs.
    --input-file: Import one dashboard JSON file.
    --folder: Import dashboards into a Grafana folder by name or nested path.

Example:
    GRAFANA_URL=https://grafana.example.com GRAFANA_AUTH='<token>' \\
        python3 scripts/sync_grafana_resources.py export-alert-rules \\
        --rule-group 'StreamingTech Tasks Running' \\
        --output-dir grafana_alert_rules
"""

from __future__ import annotations

import argparse
import base64
import copy
import difflib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class GrafanaClient:
    """Minimal Grafana API client using Python's standard library."""

    def __init__(self, url: str, auth: str) -> None:
        self.url = url.rstrip("/")
        self.authorization = authorization_header(auth)

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        payload = self.request_text(method, path, body, extra_headers)
        return json.loads(payload) if payload else None

    def request_text(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        """Return an API response without changing its serialized form."""
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Accept": "application/json",
            "Authorization": self.authorization,
        }
        if extra_headers:
            headers.update(extra_headers)
        if data is not None:
            headers["Content-Type"] = "application/json"

        request = Request(f"{self.url}{path}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310
                payload = response.read().decode()
        except HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise RuntimeError(f"Grafana API {method} {path} returned {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"Unable to reach Grafana at {self.url}: {error}") from error

        return payload


def authorization_header(auth: str) -> str:
    """Match Grafana Terraform auth handling for tokens and user:password."""
    auth = auth.strip()
    if not auth:
        raise ValueError("Grafana authentication value must not be empty.")

    # Keep accepting explicit Authorization header values for compatibility,
    # while matching the Terraform provider's documented raw auth formats.
    if auth.lower().startswith(("bearer ", "basic ")):
        return auth
    if ":" in auth:
        return "Basic " + base64.b64encode(auth.encode()).decode()
    # Legacy Grafana API keys and service-account tokens both use Bearer auth.
    return f"Bearer {auth}"


def output_filename(value: str) -> str:
    """Return a readable, portable filename component."""
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-.") or "unnamed"


def approximate_frontend_save_model(dashboard: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Apply observed frontend defaults missing from Grafana's migrated API model.

    Grafana's UI creates a DashboardModel before makeExportableV1. Plugin state and
    viewport-dependent migrations cannot be reconstructed exactly from HTTP data.
    """
    model = copy.deepcopy(dashboard)
    model.pop("id", None)
    if not model.get("timezone"):
        model["timezone"] = "browser"

    def add_threshold_defaults(value: Any) -> None:
        if isinstance(value, dict):
            thresholds = value.get("thresholds")
            if isinstance(thresholds, dict):
                steps = thresholds.get("steps", [])
                if steps and isinstance(steps[0], dict):
                    steps[0].setdefault("value", 0)
            for child in value.values():
                add_threshold_defaults(child)
        elif isinstance(value, list):
            for child in value:
                add_threshold_defaults(child)

    for panel in model.get("panels", []):
        if panel.get("description") == "":
            panel.pop("description")
        for target in panel.get("targets", []):
            if target.get("hide") is False:
                target.pop("hide")
        add_threshold_defaults(panel.get("fieldConfig", {}))
        plugin = settings["panels"].get(panel.get("type"))
        if plugin and plugin.get("signature") == "internal":
            panel["pluginVersion"] = settings["buildInfo"]["version"]
        if panel.get("type") == "timeseries":
            defaults = panel.get("fieldConfig", {}).get("defaults", {})
            custom = defaults.get("custom")
            if custom is not None and "showValues" not in custom:
                custom["showValues"] = False
                defaults["custom"] = dict(sorted(custom.items()))
    for variable in model.get("templating", {}).get("list", []):
        reference = variable.get("datasource")
        datasource_type = reference.get("type") if isinstance(reference, dict) else reference
        if variable.get("type") == "query" and datasource_type == "loki":
            variable.setdefault("regexApplyTo", "value")
            variable["type"] = variable.pop("type")
    return model


def externalize_dashboard(dashboard: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Mirror makeExportableV1's datasource and export metadata transformation."""
    exported = copy.deepcopy(dashboard)
    exported.pop("id", None)
    inputs = {item["name"]: item for item in exported.get("__inputs", [])}
    requires = {(item["type"], item["id"]): item for item in exported.get("__requires", [])}
    datasources = {item["uid"]: item for item in settings["datasources"].values() if item.get("uid")}
    datasources.update({item["name"]: item for item in settings["datasources"].values()})
    variables = {item["name"]: item for item in exported.get("templating", {}).get("list", [])}
    datasource_variable_refs: dict[str, str] = {}

    def template_datasource(obj: dict[str, Any], fallback: Any = None) -> None:
        if "datasource" not in obj:
            if fallback is not None:
                obj["datasource"] = copy.deepcopy(fallback)
            return
        reference = obj.get("datasource")
        uid = reference.get("uid") if isinstance(reference, dict) else reference
        if not isinstance(uid, str):
            return
        variable_match = re.fullmatch(r"\$(?:\{([^}]+)\}|([A-Za-z_][A-Za-z0-9_]*))", uid)
        variable_name = variable_match.group(1) or variable_match.group(2) if variable_match else None
        datasource_variable = variables.get(variable_name) if variable_name else None
        if datasource_variable and datasource_variable.get("type") == "datasource":
            if variable_name in datasource_variable_refs:
                return
            current = datasource_variable.get("current", {}).get("value")
            if not isinstance(current, str):
                return
            uid = current
        elif uid.startswith("$"):
            return
        datasource = datasources.get(uid)
        if not datasource or datasource.get("meta", {}).get("builtIn"):
            return
        meta = datasource["meta"]
        plugin_id = meta["id"]
        name = "DS_" + datasource["name"].replace(" ", "_").upper()
        inputs[name] = {
            "name": name,
            "label": datasource["name"],
            "description": "",
            "type": "datasource",
            "pluginId": plugin_id,
            "pluginName": meta["name"],
        }
        requires[("datasource", plugin_id)] = {
            "type": "datasource",
            "id": plugin_id,
            "name": meta["name"],
            "version": meta.get("info", {}).get("version") or "1.0.0",
        }
        placeholder = "${" + name + "}"
        if datasource_variable and variable_name:
            datasource_variable_refs[variable_name] = placeholder
        else:
            obj["datasource"] = {"type": plugin_id, "uid": placeholder}

    def process_panel(panel: dict[str, Any]) -> None:
        if panel.get("libraryPanel"):
            raise RuntimeError("External export of library panels requires Grafana's frontend library-panel loader.")
        panel_type = panel.get("type")
        if panel_type != "row":
            template_datasource(panel)
            for target in panel.get("targets", []):
                template_datasource(target, panel.get("datasource"))
            plugin = settings["panels"].get(panel_type)
            if plugin:
                requires[("panel", panel_type)] = {
                    "type": "panel",
                    "id": panel_type,
                    "name": plugin["name"],
                    "version": plugin.get("info", {}).get("version", ""),
                }
        if panel.get("collapsed"):
            for child in panel.get("panels", []):
                process_panel(child)

    for panel in exported.get("panels", []):
        process_panel(panel)
    for variable in exported.get("templating", {}).get("list", []):
        if variable.get("type") == "query":
            template_datasource(variable)
            variable["options"] = []
            variable["current"] = {}
            if variable.get("refresh") == 0:
                variable["refresh"] = 1
        elif variable.get("type") == "datasource":
            placeholder = datasource_variable_refs.get(variable["name"])
            variable["current"] = {"text": "", "value": placeholder, "selected": True} if placeholder else {}
        elif variable.get("type") == "adhoc":
            template_datasource(variable)
    for annotation in exported.get("annotations", {}).get("list", []):
        template_datasource(annotation)

    for variable in exported.get("templating", {}).get("list", []):
        if variable.get("type") == "constant":
            name = "VAR_" + variable["name"].replace(" ", "_").upper()
            inputs[name] = {
                "name": name,
                "type": "constant",
                "label": variable.get("label") or variable["name"],
                "value": variable["query"],
                "description": "",
            }
            variable["query"] = "${" + name + "}"
            variable["current"] = {"value": variable["query"], "text": variable["query"], "selected": False}
            variable["options"] = [variable["current"]]

    requires[("grafana", "grafana")] = {
        "type": "grafana",
        "id": "grafana",
        "name": "Grafana",
        "version": settings["buildInfo"]["version"],
    }
    elements = exported.pop("__elements", {})
    exported.pop("__inputs", None)
    exported.pop("__requires", None)
    return {
        "__inputs": list(inputs.values()),
        "__elements": elements,
        "__requires": sorted(requires.values(), key=lambda item: item["id"]),
        **exported,
    }


def migrated_dashboard_for_export(client: GrafanaClient, dashboard: dict[str, Any]) -> dict[str, Any]:
    """Use Grafana's migrated dashboard API as the base for an external export."""
    uid = dashboard["uid"]
    resource = client.request(
        "GET", f"/apis/dashboard.grafana.app/v1beta1/namespaces/default/dashboards/{quote(uid, safe='')}"
    )
    migrated = resource["spec"]
    week_start = migrated.pop("weekStart", None)
    migrated["uid"] = uid
    migrated["version"] = dashboard["version"]
    if week_start is not None:
        migrated["weekStart"] = week_start
    return migrated


def prepare_dashboard_import(dashboard: dict[str, Any], datasources: list[dict[str, Any]]) -> dict[str, Any]:
    """Resolve external-sharing inputs before using the dashboard save API."""
    prepared = copy.deepcopy(dashboard)
    replacements = {}
    for item in prepared.get("__inputs", []):
        if item["type"] == "constant":
            replacements[item["name"]] = str(item["value"])
        elif item["type"] == "datasource":
            matches = [
                datasource
                for datasource in datasources
                if datasource["name"] == item["label"] and datasource["type"] == item["pluginId"]
            ]
            if len(matches) != 1:
                raise RuntimeError(f"Unable to resolve datasource input {item['name']!r} ({item['label']}).")
            replacements[item["name"]] = matches[0]["uid"]

    def replace(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, str):
            for name, replacement in replacements.items():
                value = value.replace("${" + name + "}", replacement)
        return value

    prepared = replace(prepared)
    for key in ("__inputs", "__elements", "__requires"):
        prepared.pop(key, None)
    prepared["id"] = None
    return prepared


def write_json(path: Path, value: Any, trailing_newline: bool = True) -> None:
    """Write formatted JSON suitable for source control."""
    content = formatted_json(value)
    path.write_text(content if trailing_newline else content.rstrip("\n"))


def formatted_json(value: Any) -> str:
    """Serialize JSON using the same formatting as Grafana UI downloads."""
    return json.dumps(value, ensure_ascii=False, indent=4) + "\n"


def export_alert_rules(args: argparse.Namespace, client: GrafanaClient) -> None:
    rules = client.request("GET", "/api/v1/provisioning/alert-rules")
    selected = [rule for rule in rules if rule["ruleGroup"] == args.rule_group]
    if args.folder_uid:
        selected = [rule for rule in selected if rule.get("folderUID") == args.folder_uid]
    if not selected:
        raise RuntimeError("No alert rules matched the requested rule group and folder.")

    folder_uids = {rule["folderUID"] for rule in selected}
    if len(folder_uids) != 1:
        raise RuntimeError("The rule-group name exists in multiple folders; provide --folder-uid.")
    folder_uid = folder_uids.pop()
    group_path = (
        f"/api/v1/provisioning/folder/{quote(folder_uid, safe='')}/rule-groups/"
        f"{quote(args.rule_group, safe='')}/export?format=json"
    )
    payload = client.request_text("GET", group_path)
    document = json.loads(payload)
    validate_alert_rule_group_document(document, Path("Grafana API response"))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{output_filename(args.rule_group).lower()}.json"
    write_json(path, document)
    print(f"Exported alert rule group: {path}")


def validate_alert_rule_group_document(document: Any, path: Path) -> dict[str, Any]:
    """Validate a native Grafana alert-rule group export."""
    if not isinstance(document, dict) or document.get("apiVersion") != 1:
        raise RuntimeError(f"{path} is not a Grafana alerting provisioning document.")
    groups = document.get("groups")
    if not isinstance(groups, list) or len(groups) != 1 or not isinstance(groups[0], dict):
        raise RuntimeError(f"{path} must contain exactly one alert-rule group.")
    group = groups[0]
    if not group.get("name") or not group.get("folder") or not isinstance(group.get("rules"), list):
        raise RuntimeError(f"{path} contains an incomplete alert-rule group.")
    return group


def load_alert_rule_group(path: Path) -> dict[str, Any]:
    """Load one native Grafana alert-rule group export."""
    document = json.loads(path.read_text())
    return validate_alert_rule_group_document(document, path)


def folder_paths(client: GrafanaClient) -> dict[str, str]:
    """Map Grafana folder paths, including nested parents, to folder UIDs."""
    query = urlencode({"type": "dash-folder", "limit": 1000})
    folders = client.request("GET", f"/api/search?{query}")
    by_uid = {folder["uid"]: folder for folder in folders if folder.get("uid")}

    def full_path(folder: dict[str, Any]) -> str:
        titles = [folder["title"]]
        parent_uid = folder.get("folderUid")
        seen: set[str] = set()
        while parent_uid and parent_uid in by_uid and parent_uid not in seen:
            seen.add(parent_uid)
            parent = by_uid[parent_uid]
            titles.append(parent["title"])
            parent_uid = parent.get("folderUid")
        return "/".join(reversed(titles))

    return {full_path(folder): uid for uid, folder in by_uid.items()}


def resolve_folder_uid(group: dict[str, Any], override: str | None, client: GrafanaClient) -> str:
    """Resolve a native export's folder path to the UID required by the API."""
    if override:
        return override
    paths = folder_paths(client)
    folder_uid = paths.get(group["folder"])
    if folder_uid:
        return folder_uid
    raise RuntimeError(f"Unable to resolve Grafana folder {group['folder']!r}; provide --folder-uid.")


def duration_seconds(value: Any) -> int:
    """Convert a Grafana duration string to whole seconds."""
    if isinstance(value, int):
        return value
    if not isinstance(value, str):
        raise RuntimeError(f"Unsupported alert-rule group interval: {value!r}")
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    parts = re.findall(r"(\d+)([smhdw])", value)
    if not parts or "".join(f"{amount}{unit}" for amount, unit in parts) != value:
        raise RuntimeError(f"Unsupported alert-rule group interval: {value!r}")
    return sum(int(amount) * units[unit] for amount, unit in parts)


def alert_rule_group_payload(group: dict[str, Any], folder_uid: str) -> dict[str, Any]:
    """Convert Grafana's file-export schema to its rule-group API schema."""
    rules = []
    for exported_rule in group["rules"]:
        rule = dict(exported_rule)
        rule["orgID"] = group.get("orgId", 1)
        rule["folderUID"] = folder_uid
        rule["ruleGroup"] = group["name"]
        for query in rule.get("data", []):
            query.setdefault("queryType", "")
        rules.append(rule)
    return {
        "title": group["name"],
        "folderUid": folder_uid,
        "interval": duration_seconds(group["interval"]),
        "rules": rules,
    }


def print_alert_rule_group_diff(
    path: Path,
    group: dict[str, Any],
    group_path: str,
    exists: bool,
    client: GrafanaClient,
) -> bool:
    """Print the changes between a local group export and the live group."""
    local_text = formatted_json({"apiVersion": 1, "groups": [group]})
    remote_text = ""
    if exists:
        remote = client.request("GET", f"{group_path}/export?format=json")
        remote_text = formatted_json(remote)
    diff = list(
        difflib.unified_diff(
            remote_text.splitlines(keepends=True),
            local_text.splitlines(keepends=True),
            fromfile=f"grafana:{group['folder']}/{group['name']}",
            tofile=str(path),
        )
    )
    if not diff:
        print(f"No changes for alert rule group: {group['name']}")
        return False
    sys.stdout.writelines(diff)
    return True


def import_alert_rules(args: argparse.Namespace, client: GrafanaClient) -> None:
    path = Path(args.input_file)
    group = load_alert_rule_group(path)
    folder_uid = resolve_folder_uid(group, args.folder_uid, client)
    group_path = f"/api/v1/provisioning/folder/{quote(folder_uid, safe='')}/rule-groups/{quote(group['name'], safe='')}"
    try:
        client.request("GET", group_path)
        exists = True
    except RuntimeError as error:
        if "returned 404:" not in str(error):
            raise
        exists = False

    if exists and not args.overwrite:
        print(f"Skipped existing alert rule group (use --overwrite): {group['name']}")
    elif args.dry_run:
        operation = "update" if exists else "create"
        changed = print_alert_rule_group_diff(path, group, group_path, exists, client)
        if changed:
            print(f"Would {operation} alert rule group: {group['name']} ({len(group['rules'])} rules)")
    else:
        payload = alert_rule_group_payload(group, folder_uid)
        client.request(
            "PUT",
            group_path,
            payload,
            {"X-Disable-Provenance": "true"},
        )
        operation = "Updated" if exists else "Created"
        print(f"{operation} alert rule group: {group['name']}")


def export_dashboards(args: argparse.Namespace, client: GrafanaClient) -> None:
    if args.output_file and (not args.uids or len(set(args.uids)) != 1):
        raise ValueError("--output-file requires exactly one --uid.")
    if args.uids:
        uids = list(dict.fromkeys(args.uids))
    else:
        query = urlencode({"type": "dash-db", "limit": 500})
        dashboards = client.request("GET", f"/api/search?{query}")
        uids = [summary["uid"] for summary in dashboards if summary.get("uid")]

    exports = []
    for uid in uids:
        response = client.request("GET", f"/api/dashboards/uid/{quote(uid, safe='')}")
        dashboard = response["dashboard"]
        if dashboard.get("uid") != uid:
            raise RuntimeError(f"Grafana returned an unexpected dashboard UID for {uid}.")
        exports.append(dashboard)

    settings = client.request("GET", "/api/frontend/settings") if args.for_sharing_externally else None
    for dashboard in exports:
        if settings is not None:
            model = migrated_dashboard_for_export(client, dashboard)
            dashboard = externalize_dashboard(approximate_frontend_save_model(model, settings), settings)
        path = (
            Path(args.output_file)
            if args.output_file
            else Path(args.output_dir)
            / f"{output_filename(dashboard['uid'])}--{output_filename(dashboard['title'])}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json(path, dashboard, trailing_newline=not args.for_sharing_externally)
        print(f"Exported dashboard: {path}")


def import_dashboards(args: argparse.Namespace, client: GrafanaClient) -> None:
    files = [Path(args.input_file)] if args.input_file else sorted(Path(args.input_dir).glob("*.json"))
    if args.input_file and not files[0].is_file():
        raise RuntimeError(f"Dashboard JSON file not found: {files[0]}.")
    if not files:
        raise RuntimeError(f"No dashboard JSON files found in {args.input_dir}.")

    folder_uid = args.folder_uid
    if args.folder and not args.dry_run:
        folder_uid = folder_paths(client).get(args.folder)
        if not folder_uid:
            raise RuntimeError(f"Unable to resolve Grafana folder {args.folder!r}.")

    for path in files:
        dashboard = json.loads(path.read_text())
        if not isinstance(dashboard, dict) or not dashboard.get("title"):
            raise RuntimeError(f"{path} is not a Grafana dashboard JSON document.")
        if args.dry_run:
            print(f"Would import dashboard: {dashboard['title']}")
            continue
        if dashboard.get("__inputs"):
            dashboard = prepare_dashboard_import(dashboard, client.request("GET", "/api/datasources"))
        payload: dict[str, Any] = {"dashboard": dashboard, "overwrite": args.overwrite}
        if folder_uid:
            payload["folderUid"] = folder_uid
        client.request("POST", "/api/dashboards/db", payload)
        print(f"Imported dashboard: {dashboard['title']}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export or import Grafana resources.")
    parser.add_argument("--url", default=os.getenv("GRAFANA_URL"))
    parser.add_argument(
        "--auth",
        default=os.getenv("GRAFANA_AUTH"),
        help=("Grafana API key, service-account token, or username:password; defaults to GRAFANA_AUTH"),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    export_rules = commands.add_parser("export-alert-rules")
    export_rules.add_argument("--rule-group", required=True)
    export_rules.add_argument("--folder-uid")
    export_rules.add_argument("--output-dir", default="grafana_alert_rules")

    import_rules = commands.add_parser("import-alert-rules")
    import_rules.add_argument("input_file", help="Grafana alert-rule group JSON file")
    import_rules.add_argument("--folder-uid")
    import_rules.add_argument("--overwrite", action="store_true")
    import_rules.add_argument("--dry-run", action="store_true")

    export_dashboards_parser = commands.add_parser("export-dashboards")
    export_dashboards_parser.add_argument("--output-dir", default="grafana_dashboards")
    export_dashboards_parser.add_argument("--output-file", help="Write one selected dashboard to this JSON file.")
    export_dashboards_parser.add_argument("--for-sharing-externally", action="store_true")
    export_dashboards_parser.add_argument(
        "--uid", dest="uids", action="append", help="Export only this dashboard UID; repeat for multiple dashboards."
    )

    import_dashboards_parser = commands.add_parser("import-dashboards")
    import_dashboards_parser.add_argument("--input-dir", default="grafana_dashboards")
    import_dashboards_parser.add_argument("--input-file", help="Import one dashboard JSON file.")
    dashboard_folder = import_dashboards_parser.add_mutually_exclusive_group()
    dashboard_folder.add_argument(
        "--folder", help="Grafana folder name or nested path, for example Applications/StreamingTech."
    )
    dashboard_folder.add_argument("--folder-uid", help="Grafana folder UID.")
    import_dashboards_parser.add_argument("--overwrite", action="store_true")
    import_dashboards_parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()
    if not args.url or not args.auth:
        parser.error("--url/--auth or GRAFANA_URL/GRAFANA_AUTH must be provided.")
    return args


def main() -> int:
    args = parse_args()
    client = GrafanaClient(args.url, args.auth)
    handlers = {
        "export-alert-rules": export_alert_rules,
        "import-alert-rules": import_alert_rules,
        "export-dashboards": export_dashboards,
        "import-dashboards": import_dashboards,
    }
    try:
        handlers[args.command](args, client)
    except (RuntimeError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
