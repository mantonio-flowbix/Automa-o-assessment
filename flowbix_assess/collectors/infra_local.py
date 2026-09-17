"""Ingests JSON files produced by probe/flowbix_probe.py.

This is intentionally NOT an SSH client. The probe script runs on the
client's machines (manually over SSH today, optionally via an orchestrator
later) and produces one JSON file per host; this collector just reads
whatever lands in a directory and indexes it by hostname. That decoupling
means the rule engine doesn't care whether the JSON arrived via scp, a
pasted terminal output saved to a file, or an automated SSH exec.
"""
from __future__ import annotations

import json
from pathlib import Path


def collect(infra_dir: str) -> dict:
    directory = Path(infra_dir)
    if not directory.is_dir():
        raise FileNotFoundError(f"infra dir not found: {infra_dir}")

    hosts = {}
    errors = {}
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors[path.name] = str(exc)
            continue
        host_name = data.get("host") or path.stem
        hosts[host_name] = data

    return {"hosts": hosts, "_file_errors": errors}
