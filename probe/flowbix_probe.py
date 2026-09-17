#!/usr/bin/env python3
"""Standalone infra probe for the Flowbix Zabbix/Infra assessment tool.

Design goals:
  - Zero third-party dependencies (stdlib only) — safe to drop on a
    production Zabbix server/proxy without installing anything.
  - Read-only: never writes, changes config, or restarts anything.
  - Single JSON blob on stdout, so it works the same whether you:
      a) run it over an interactive SSH session and copy the output, or
      b) redirect it to a file and scp the file back, or
      c) an orchestrator executes it remotely and reads stdout directly.

Usage:
    python3 flowbix_probe.py                # JSON to stdout
    python3 flowbix_probe.py > host_XYZ.json # save to a file to scp back

Only needs read access to /proc, /etc/os-release, and (optionally) `ss`/
`netstat` and the zabbix_server/zabbix_proxy/zabbix_agentd binaries if
present on PATH. Missing pieces are reported as null, never guessed.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time

EXPECTED_ZABBIX_PORTS = {
    10050: "zabbix-agent",
    10051: "zabbix-server/proxy (trapper)",
    10060: "zabbix-agent -> proxy",
    10061: "proxy -> server",
}


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if out.returncode != 0:
            return None
        return out.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def get_os() -> dict:
    info = {"name": None, "version": None, "pretty_name": None}
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            data = dict(
                line.strip().split("=", 1)
                for line in f
                if "=" in line and not line.startswith("#")
            )
        info["name"] = data.get("ID", "").strip('"') or None
        info["version"] = data.get("VERSION_ID", "").strip('"') or None
        info["pretty_name"] = data.get("PRETTY_NAME", "").strip('"') or None
    except FileNotFoundError:
        info["pretty_name"] = platform.platform()
    return info


def get_cpu() -> dict:
    cpu_count = os.cpu_count() or 1
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        load1 = load5 = load15 = None
    return {
        "count": cpu_count,
        "load_avg_1": load1,
        "load_avg_5": load5,
        "load_avg_15": load15,
        "load_pct_1min": round(load1 / cpu_count * 100, 1) if load1 is not None else None,
    }


def get_memory() -> dict:
    try:
        meminfo = {}
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                key, _, rest = line.partition(":")
                meminfo[key] = int(rest.strip().split()[0])  # kB
        total_kb = meminfo.get("MemTotal", 0)
        available_kb = meminfo.get("MemAvailable", 0)
        used_pct = round((1 - available_kb / total_kb) * 100, 1) if total_kb else None
        return {
            "total_mb": round(total_kb / 1024, 1),
            "available_mb": round(available_kb / 1024, 1),
            "used_pct": used_pct,
        }
    except FileNotFoundError:
        return {"total_mb": None, "available_mb": None, "used_pct": None}


def get_disk(paths: list[str]) -> list[dict]:
    results = []
    for path in paths:
        try:
            usage = shutil.disk_usage(path)
            results.append({
                "path": path,
                "total_gb": round(usage.total / (1024 ** 3), 2),
                "used_gb": round(usage.used / (1024 ** 3), 2),
                "used_pct": round(usage.used / usage.total * 100, 1),
            })
        except FileNotFoundError:
            continue
    return results


def get_zabbix_binary_versions() -> dict:
    versions = {}
    for binary in ("zabbix_server", "zabbix_proxy", "zabbix_agentd", "zabbix_agent2"):
        path = shutil.which(binary)
        if not path:
            continue
        out = _run([binary, "-V"])
        if out:
            match = re.search(r"(\d+\.\d+\.\d+)", out.splitlines()[0])
            versions[binary] = match.group(1) if match else out.splitlines()[0]
    return versions


def get_listening_ports() -> list[int]:
    out = _run(["ss", "-ltn"]) or _run(["netstat", "-ltn"])
    if not out:
        return []
    ports = set()
    for line in out.splitlines():
        match = re.search(r":(\d+)\s*$", line.split()[3]) if len(line.split()) > 3 else None
        if match:
            ports.add(int(match.group(1)))
    return sorted(p for p in ports if p in EXPECTED_ZABBIX_PORTS or p == 3306)


def get_uptime_seconds() -> float | None:
    try:
        with open("/proc/uptime", encoding="utf-8") as f:
            return float(f.read().split()[0])
    except FileNotFoundError:
        return None


def main():
    extra_disk_paths = sys.argv[1:] or []
    disk_paths = ["/"] + extra_disk_paths

    payload = {
        "host": socket.gethostname(),
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "os": get_os(),
        "cpu": get_cpu(),
        "memory": get_memory(),
        "disk": get_disk(disk_paths),
        "zabbix_binary_versions": get_zabbix_binary_versions(),
        "listening_ports": get_listening_ports(),
        "uptime_seconds": get_uptime_seconds(),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
