from __future__ import annotations

import os
import re
import yaml

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value, extra_env: dict | None = None):
    if isinstance(value, str):
        def repl(match):
            var = match.group(1)
            if extra_env and var in extra_env:
                return extra_env[var]
            return os.environ.get(var, "")
        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v, extra_env) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v, extra_env) for v in value]
    return value


DEFAULT_THRESHOLDS = {
    "cpu_warning_pct": 50,
    "cpu_critical_pct": 70,
    "short_interval_seconds": 60,
    "max_preprocessing_steps": 3,
    "max_discovery_rules_per_template": 5,
    "history_table_size_gb_warning": 500,
    "unsupported_items_warning_count": 200,
    "memory_warning_pct": 80,
    "memory_critical_pct": 90,
    "disk_warning_pct": 80,
    "disk_critical_pct": 90,
    "mysql_eol_versions": {
        "5.7": "2023-10-31",
        "8.0": "2026-04-30",
    },
    "expected_ports_by_role": {
        "server": [10051],
        "proxy": [10061],
    },
    "process_busy_warning_pct": 65,
    "process_busy_critical_pct": 85,
    "mysql_min_connections_recommended": 150,
    # Minimum MySQL major.minor per Zabbix major.minor — starting point only,
    # confirm against https://www.zabbix.com/documentation before relying on
    # it for a go/no-go call. Override via thresholds.zabbix_mysql_compat.
    "zabbix_mysql_compat": {
        "7.0": "8.0",
        "6.4": "5.7",
        "6.0": "5.7",
    },
    "db_growth_gb_per_month_warning": 100,
}


class Config:
    def __init__(self, raw: dict):
        self.raw = raw
        self.client = raw.get("client", {})
        self.zabbix = raw.get("zabbix")
        self.mysql = raw.get("mysql")
        self.grafana = raw.get("grafana")
        self.infra = raw.get("infra") or {}
        self.thresholds = {**DEFAULT_THRESHOLDS, **(raw.get("thresholds") or {})}

    @property
    def client_name(self) -> str:
        return self.client.get("name", "Cliente")

    @property
    def infra_hosts(self) -> list:
        """List of {"name": ..., "role": "server"|"proxy"|...} from config.infra.hosts."""
        return self.infra.get("hosts", [])

    @property
    def infra_dir(self) -> str | None:
        return self.infra.get("collected_dir")

    @property
    def target_zabbix_version(self) -> str:
        return self.client.get("target_zabbix_version", "7.0")

    @classmethod
    def load(cls, path: str) -> "Config":
        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        raw = _expand_env(raw)
        return cls(raw)

    @classmethod
    def from_raw(cls, raw: dict, extra_env: dict | None = None) -> "Config":
        """Like `load()`, but for a dict already in memory (e.g. the
        webapp's `ClientStore.load_config_raw()`). `extra_env` is checked
        before `os.environ` — used to resolve `${VAR}` placeholders against
        a per-client `.env` file instead of the process environment."""
        return cls(_expand_env(raw, extra_env))
