#!/usr/bin/env python3
"""Provision the standard SigNoz alert set for a service.

The four standard alerts (error rate, new exception, p95 latency, ERROR-log
heartbeat — see docs/observability-signoz.md) are created once by hand for a
reference service, exported here as JSON templates, and re-created for each new
service with the service name substituted:

    export SIGNOZ_URL=https://signoz.example.com
    export SIGNOZ_API_KEY=...   # Settings -> API Keys (admin role for rule writes)

    # Once: snapshot the reference service's rules into committed templates.
    scripts/signoz-alerts.py export aperture

    # Per new project: re-create the rules for the new service.
    scripts/signoz-alerts.py provision myapp [--dry-run]

Templates live in scripts/signoz-alert-templates/ with the service name
replaced by the {{SERVICE}} placeholder. Provisioning is idempotent: a template
whose substituted alert name already exists in SigNoz is skipped. Stdlib only —
no dependencies to install wherever this runs.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PLACEHOLDER = "{{SERVICE}}"
TEMPLATE_DIR = Path(__file__).parent / "signoz-alert-templates"

# Server-assigned fields that must not be replayed when creating a rule.
_SERVER_FIELDS = ("id", "state", "createAt", "createBy", "updateAt", "updateBy")


def _api(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    base = os.environ.get("SIGNOZ_URL")
    key = os.environ.get("SIGNOZ_API_KEY")
    if not base or not key:
        sys.exit("Set SIGNOZ_URL and SIGNOZ_API_KEY (see docs/observability-signoz.md)")
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"SIGNOZ-API-KEY": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        sys.exit(f"{method} {path} failed: HTTP {error.code}\n{error.read().decode()}")


def _list_rules() -> list[dict[str, Any]]:
    data = _api("GET", "/api/v1/rules").get("data")
    # Older SigNoz versions wrap the list in {"rules": [...]}.
    return data.get("rules", []) if isinstance(data, dict) else data or []


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def export_rules(service: str) -> None:
    """Snapshot every rule mentioning the service as a {{SERVICE}} template."""
    pattern = re.compile(rf"\b{re.escape(service)}\b")
    exported = 0
    TEMPLATE_DIR.mkdir(exist_ok=True)
    for rule in _list_rules():
        text = json.dumps(
            {k: v for k, v in rule.items() if k not in _SERVER_FIELDS},
            indent=2,
            sort_keys=True,
        )
        if not pattern.search(text):
            continue
        templated = pattern.sub(PLACEHOLDER, text)
        name = _slug(
            json.loads(templated).get("alert", "rule").replace(PLACEHOLDER, "service")
        )
        path = TEMPLATE_DIR / f"{name}.json"
        path.write_text(templated + "\n")
        print(f"exported {rule.get('alert')!r} -> {path}")
        exported += 1
    if not exported:
        sys.exit(
            f"No rules mention service {service!r}; create them in the SigNoz UI first"
        )
    print(f"{exported} template(s) written — review and commit them")


def provision_rules(service: str, dry_run: bool) -> None:
    """POST each template with the service name substituted; skip existing rules."""
    templates = sorted(TEMPLATE_DIR.glob("*.json"))
    if not templates:
        sys.exit(f"No templates in {TEMPLATE_DIR}; run the export subcommand first")
    existing = {rule.get("alert") for rule in _list_rules()}
    for path in templates:
        text = path.read_text()
        if PLACEHOLDER not in text:
            print(f"skipped {path.name}: no {PLACEHOLDER} placeholder")
            continue
        rule = json.loads(text.replace(PLACEHOLDER, service))
        if rule.get("alert") in existing:
            print(f"exists  {rule['alert']!r}")
            continue
        if dry_run:
            print(f"would create {rule.get('alert')!r} from {path.name}")
            continue
        _api("POST", "/api/v1/rules", rule)
        print(f"created {rule.get('alert')!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "export", help="write templates from an existing service's rules"
    ).add_argument("service", help="reference service whose rules become the templates")
    provision = commands.add_parser(
        "provision", help="create the standard rules for a service"
    )
    provision.add_argument(
        "service", help="service name to substitute into the templates"
    )
    provision.add_argument(
        "--dry-run", action="store_true", help="print without creating"
    )
    args = parser.parse_args()

    if args.command == "export":
        export_rules(args.service)
    else:
        provision_rules(args.service, args.dry_run)


if __name__ == "__main__":
    main()
