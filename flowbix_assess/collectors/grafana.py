"""Grafana HTTP API collector.

Note: Grafana's own storage backend (SQLite vs MySQL/MariaDB for its
internal DB) is not exposed by the API — it only lives in grafana.ini. If
`grafana.backend_db_type` is set in the config (filled in manually, or by a
future SSH-based collector), it's passed through as-is; otherwise the
report flags it for manual verification.
"""
from __future__ import annotations

import requests


def collect(grafana_config: dict) -> dict:
    base_url = grafana_config["url"].rstrip("/")
    headers = {"Authorization": f"Bearer {grafana_config['api_key']}"}
    verify_ssl = grafana_config.get("verify_ssl", True)
    timeout = grafana_config.get("timeout", 30)

    result: dict = {}

    health = requests.get(f"{base_url}/api/health", headers=headers, verify=verify_ssl, timeout=timeout)
    health.raise_for_status()
    health_json = health.json()
    result["version"] = health_json.get("version")

    datasources = requests.get(f"{base_url}/api/datasources", headers=headers, verify=verify_ssl, timeout=timeout)
    datasources.raise_for_status()
    result["datasources"] = datasources.json()

    dashboards = requests.get(
        f"{base_url}/api/search",
        params={"type": "dash-db", "limit": 5000},
        headers=headers,
        verify=verify_ssl,
        timeout=timeout,
    )
    dashboards.raise_for_status()
    result["dashboards"] = dashboards.json()

    result["backend_db_type"] = grafana_config.get("backend_db_type")

    return result
