"""Mock-based tests for flowbix_assess.collectors.zabbix.

No real Zabbix server needed:
- URL-building and error handling are tested against `ZabbixClient._raw_call`
  by patching `requests.Session.post` with a fake response.
- `collect()`'s orchestration (which methods it calls, with what params, and
  how it assembles the result) is tested by patching `ZabbixClient.call`/
  `.version` directly — this is the exact seam where the "pasted the wrong
  Zabbix URL" and "API token expired" failures showed up in production, so
  pinning down the request-building logic here catches regressions early.
"""
from __future__ import annotations

import pytest

from flowbix_assess.collectors import zabbix


class _FakeResponse:
    def __init__(self, json_data, status_ok=True):
        self._json = json_data
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("HTTP error")

    def json(self):
        return self._json


def _client_with_fake_post(monkeypatch, json_data):
    captured = {}

    def fake_post(self, url, json=None, headers=None, verify=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse(json_data)

    monkeypatch.setattr(zabbix.requests.Session, "post", fake_post)
    client = zabbix.ZabbixClient(url="https://zabbix.example.com", token="fake-token")
    return client, captured


@pytest.mark.parametrize("base_url,expected", [
    ("https://zabbix.example.com", "https://zabbix.example.com/api_jsonrpc.php"),
    ("https://zabbix.example.com/", "https://zabbix.example.com/api_jsonrpc.php"),
    ("https://zabbix.example.com/api_jsonrpc.php", "https://zabbix.example.com/api_jsonrpc.php"),
])
def test_endpoint_url_is_normalized(monkeypatch, base_url, expected):
    captured = {}

    def fake_post(self, url, json=None, headers=None, verify=None, timeout=None):
        captured["url"] = url
        return _FakeResponse({"result": "7.0.27"})

    monkeypatch.setattr(zabbix.requests.Session, "post", fake_post)
    client = zabbix.ZabbixClient(url=base_url, token="fake-token")
    client.version()

    assert captured["url"] == expected


def test_raw_call_raises_on_jsonrpc_error(monkeypatch):
    client, _ = _client_with_fake_post(monkeypatch, {"error": {"code": -32500, "message": "Application error.", "data": "API token expired."}})
    with pytest.raises(zabbix.ZabbixAPIError, match="API token expired"):
        client.call("host.get", {})


def test_call_sends_bearer_token(monkeypatch):
    client, captured = _client_with_fake_post(monkeypatch, {"result": []})
    client.call("host.get", {})
    assert captured["headers"]["Authorization"] == "Bearer fake-token"


def test_init_requires_token_or_credentials():
    with pytest.raises(ValueError):
        zabbix.ZabbixClient(url="https://zabbix.example.com")


def _fake_call(method, params=None):
    params = params or {}
    if method == "proxy.get":
        return []
    if method == "host.get":
        return [{"hostid": "1", "host": "srv1", "name": "srv1", "status": "0"}]
    if method == "item.get":
        if params.get("countOutput"):
            return 2
        if params.get("filter", {}).get("state") == 1:
            return [{"itemid": "99", "name": "broken item", "key_": "system.run[x]", "error": "timeout"}]
        if "templateids" in params:
            return [{"itemid": "10", "name": "item-1", "key_": "agent.ping", "delay": "30s",
                      "history": "7d", "trends": "365d", "value_type": "3",
                      "type": "0", "master_itemid": "0", "error": ""}]
        return []
    if method == "mediatype.get":
        return []
    if method == "dashboard.get":
        return []
    if method == "template.get":
        return [
            {"templateid": "100", "host": "Template A", "name": "Template A"},
            {"templateid": "101", "host": "Template B", "name": "Template B"},
        ]
    if method == "discoveryrule.get":
        return []
    if method == "trigger.get":
        return []
    if method == "housekeeping.get":
        return {"hk_history_global": "1"}
    raise AssertionError(f"unexpected method: {method}")


def test_collect_assembles_expected_shape(monkeypatch):
    monkeypatch.setattr(zabbix.ZabbixClient, "version", lambda self: "7.0.27")
    monkeypatch.setattr(zabbix.ZabbixClient, "call", lambda self, method, params=None: _fake_call(method, params))

    result = zabbix.collect({"url": "https://zabbix.example.com", "token": "fake-token"})

    assert result["version"] == "7.0.27"
    assert result["unsupported_items_count"] == 2
    assert len(result["unsupported_items_sample"]) == 1
    assert len(result["templates"]) == 2
    for tpl in result["templates"]:
        assert tpl["items"][0]["name"] == "item-1"
        assert tpl["discovery_rules"] == []
        assert tpl["triggers"] == []
    assert result["housekeeping"] == {"hk_history_global": "1"}


def test_collect_respects_template_limit(monkeypatch):
    monkeypatch.setattr(zabbix.ZabbixClient, "version", lambda self: "7.0.27")
    monkeypatch.setattr(zabbix.ZabbixClient, "call", lambda self, method, params=None: _fake_call(method, params))

    result = zabbix.collect({"url": "https://zabbix.example.com", "token": "fake-token"}, template_limit=1)

    assert len(result["templates"]) == 1
