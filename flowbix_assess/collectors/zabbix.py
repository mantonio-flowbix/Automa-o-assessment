"""Thin Zabbix JSON-RPC API client plus a higher-level collector.

Only read-only API methods are used (`*.get`). Nothing here ever calls a
`*.create`, `*.update` or `*.delete` method.
"""
from __future__ import annotations

import itertools
import statistics
from datetime import datetime, timedelta, timezone

import requests

DEFAULT_CPU_ITEM_KEYS = [
    "system.cpu.util",
    "system.cpu.util[,,avg1]",
    "system.cpu.util[,idle]",
]


class ZabbixAPIError(RuntimeError):
    pass


class ZabbixClient:
    def __init__(self, url: str, token: str | None = None, user: str | None = None,
                 password: str | None = None, verify_ssl: bool = True, timeout: int = 30):
        self.url = url.rstrip("/")
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._id_counter = itertools.count(1)
        self._session = requests.Session()
        self._token = token

        if not self._token:
            if not (user and password):
                raise ValueError("Provide either 'token' or 'user'+'password' for Zabbix auth")
            self._token = self._login(user, password)

    def _login(self, user: str, password: str) -> str:
        result = self._raw_call("user.login", {"username": user, "password": password}, auth=None)
        return result

    def _raw_call(self, method: str, params: dict, auth: str | None):
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": next(self._id_counter),
        }
        headers = {"Content-Type": "application/json-rpc"}
        if auth:
            headers["Authorization"] = f"Bearer {auth}"
        resp = self._session.post(
            f"{self.url}/api_jsonrpc.php" if not self.url.endswith("api_jsonrpc.php") else self.url,
            json=payload,
            headers=headers,
            verify=self.verify_ssl,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise ZabbixAPIError(f"{method} failed: {data['error']}")
        return data["result"]

    def call(self, method: str, params: dict | None = None):
        return self._raw_call(method, params or {}, auth=self._token)

    def version(self) -> str:
        return self._raw_call("apiinfo.version", {}, auth=None)


def _resolve_host_id(client: ZabbixClient, host_name: str) -> str | None:
    hosts = client.call("host.get", {"filter": {"host": [host_name]}, "output": ["hostid"]})
    if not hosts:
        hosts = client.call("host.get", {"filter": {"name": [host_name]}, "output": ["hostid"]})
    return hosts[0]["hostid"] if hosts else None


def _process_busy_stats(client: ZabbixClient, host_name: str, hours: int = 1) -> dict:
    """Avg busy% per internal Zabbix process type (poller, syncer, etc.) over
    the last `hours`, read from the host's own `zabbix[process,...]` items —
    the same internal metrics the Zabbix frontend uses for the "Utilization
    of X processes" charts. Best-effort: returns {} if the host isn't found
    or doesn't expose these items (e.g. internal monitoring not enabled).
    """
    host_id = _resolve_host_id(client, host_name)
    if not host_id:
        return {}

    items = client.call("item.get", {
        "hostids": [host_id],
        "search": {"key_": "zabbix[process,"},
        "output": ["itemid", "key_", "value_type"],
    })
    busy_items = [i for i in items if i["key_"].endswith(",avg,busy]")]
    if not busy_items:
        return {}

    time_from = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
    result = {}
    for item in busy_items:
        history = client.call("history.get", {
            "itemids": [item["itemid"]],
            "history": int(item["value_type"]),
            "time_from": time_from,
            "output": "extend",
            "sortfield": "clock",
            "sortorder": "DESC",
            "limit": 200,
        })
        values = [float(h["value"]) for h in history if h.get("value") not in (None, "")]
        if not values:
            continue
        # key_ looks like "zabbix[process,poller,avg,busy]"
        parts = item["key_"].rstrip("]").split(",")
        proc_type = parts[1] if len(parts) > 1 else item["key_"]
        result[proc_type] = round(statistics.mean(values), 1)
    return result


def _history_stats(client: ZabbixClient, host_name: str, item_keys=None, hours=24):
    """Best-effort avg/max for the first matching item key found on a host.

    Returns None if the host or a matching numeric item can't be found —
    callers should treat that as "not available" rather than an error.
    """
    item_keys = item_keys or DEFAULT_CPU_ITEM_KEYS
    hosts = client.call("host.get", {"filter": {"host": [host_name]}, "output": ["hostid"]})
    if not hosts:
        hosts = client.call("host.get", {"filter": {"name": [host_name]}, "output": ["hostid"]})
    if not hosts:
        return None
    host_id = hosts[0]["hostid"]

    items = client.call("item.get", {
        "hostids": [host_id],
        "search": {"key_": item_keys},
        "searchByAny": True,
        "output": ["itemid", "key_", "value_type"],
    })
    if not items:
        return None
    item = items[0]

    time_from = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
    history = client.call("history.get", {
        "itemids": [item["itemid"]],
        "history": int(item["value_type"]),
        "time_from": time_from,
        "output": "extend",
        "sortfield": "clock",
        "sortorder": "DESC",
        "limit": 5000,
    })
    values = [float(h["value"]) for h in history if h.get("value") not in (None, "")]
    if not values:
        return None
    return {"avg": statistics.mean(values), "max": max(values), "samples": len(values)}


def collect(zbx_config: dict, hosts_of_interest: list[str] | None = None,
            template_limit: int | None = None) -> dict:
    """Collect everything the rule engine needs from the Zabbix API.

    `hosts_of_interest` is an optional list of host/proxy names (e.g. the
    Zabbix server and its proxies) to pull CPU history for — without it
    the collector skips the per-host utilization check.
    `template_limit` caps how many templates get their items walked, useful
    for a quick smoke test against a very large environment.
    """
    client = ZabbixClient(
        url=zbx_config["url"],
        token=zbx_config.get("token"),
        user=zbx_config.get("user"),
        password=zbx_config.get("password"),
        verify_ssl=zbx_config.get("verify_ssl", True),
    )

    result: dict = {"version": client.version()}

    try:
        result["proxies"] = client.call("proxy.get", {"output": "extend"})
    except ZabbixAPIError:
        result["proxies"] = client.call("host.get", {"filter": {"status": 6}, "output": "extend"})

    result["hosts"] = client.call("host.get", {
        "output": ["hostid", "host", "name", "status"],
        "selectInterfaces": ["ip", "dns", "port", "type"],
    })

    result["unsupported_items_count"] = client.call("item.get", {
        "filter": {"state": 1},
        "countOutput": True,
    })
    result["unsupported_items_sample"] = client.call("item.get", {
        "filter": {"state": 1},
        "output": ["itemid", "name", "key_", "error"],
        "limit": 500,
    })

    result["media_types"] = client.call("mediatype.get", {"output": "extend"})

    try:
        result["dashboards"] = client.call("dashboard.get", {"output": ["dashboardid", "name"]})
    except ZabbixAPIError as exc:
        result["dashboards"] = []
        result.setdefault("_warnings", []).append(f"dashboard.get failed: {exc}")

    templates = client.call("template.get", {"output": ["templateid", "host", "name"]})
    if template_limit:
        templates = templates[:template_limit]

    for tpl in templates:
        tpl_id = tpl["templateid"]
        tpl["items"] = client.call("item.get", {
            "templateids": [tpl_id],
            "output": ["itemid", "name", "key_", "delay", "history", "trends",
                       "value_type", "type", "master_itemid", "error"],
            "selectTriggers": ["triggerid"],
            "selectPreprocessing": "extend",
        })
        tpl["discovery_rules"] = client.call("discoveryrule.get", {
            "templateids": [tpl_id],
            "output": ["itemid", "name"],
        })
        tpl["triggers"] = client.call("trigger.get", {
            "templateids": [tpl_id],
            "output": ["triggerid", "description", "expression"],
        })
    result["templates"] = templates

    result["host_cpu_stats"] = {}
    result["process_busy_stats"] = {}
    for host_name in (hosts_of_interest or []):
        stats = _history_stats(client, host_name)
        if stats:
            result["host_cpu_stats"][host_name] = stats
        busy = _process_busy_stats(client, host_name)
        if busy:
            result["process_busy_stats"][host_name] = busy

    try:
        result["housekeeping"] = client.call("housekeeping.get", {"output": "extend"})
    except ZabbixAPIError as exc:
        result["housekeeping"] = None
        result.setdefault("_warnings", []).append(f"housekeeping.get failed: {exc}")

    return result
