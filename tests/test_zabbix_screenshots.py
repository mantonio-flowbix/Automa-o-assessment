"""Tests for flowbix_assess.collectors.zabbix_screenshots.

The pure helpers are unit-tested directly. The browser flow is tested against
a tiny local HTTP server that imitates the parts of the Zabbix frontend we
touch (login form + a cookie-guarded set of screens, one of which answers
"no permissions"), so no real Zabbix is needed. Integration tests skip
themselves when Chromium isn't installed.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image

from flowbix_assess.collectors import zabbix_screenshots as zs

LOGIN_HTML = (
    '<html><body><h1>Zabbix</h1><form method="post" action="index.php">'
    '<input name="name"><input type="password" name="password">'
    '<button type="submit" id="enter" name="enter">Sign in</button></form></body></html>'
)


class FakeZabbix(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _html(self, body, status=200, headers=None):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.endswith("/index.php") or "sid=ok" not in self.headers.get("Cookie", ""):
            return self._html(LOGIN_HTML)
        action = parse_qs(parsed.query).get("action", [""])[0]
        if action == "mediatype.list":
            return self._html("<html><body><h1>Access denied</h1><p>You have no permissions to access this page.</p></body></html>")
        self._html(f"<html><body><h1>Tela {action}</h1><p>conteúdo de exemplo</p></body></html>")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode())
        if form.get("name") == ["admin"] and form.get("password") == ["secret"]:
            return self._html("", 302, {"Location": "/zabbix/zabbix.php?action=dashboard.view", "Set-Cookie": "sid=ok; Path=/"})
        self._html(LOGIN_HTML)


@pytest.fixture(scope="module")
def fake_zabbix():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeZabbix)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/zabbix"
    server.shutdown()


@pytest.fixture(autouse=True)
def fast_settle(monkeypatch):
    # The fake pages are static; the real 800ms wait is only for Zabbix widgets.
    monkeypatch.setattr(zs, "_SETTLE_MS", 50)


@pytest.fixture(scope="module")
def chromium_available():
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            pw.chromium.launch(args=["--no-sandbox"]).close()
    except Exception:  # noqa: BLE001 — any failure just means "can't run browser tests here"
        pytest.skip("Chromium (playwright) not installed")


# ---------------------------------------------------------------- helpers


@pytest.mark.parametrize("url,expected", [
    ("https://zbx.example.com/zabbix", "https://zbx.example.com/zabbix"),
    ("https://zbx.example.com/zabbix/", "https://zbx.example.com/zabbix"),
    ("https://zbx.example.com/zabbix/api_jsonrpc.php", "https://zbx.example.com/zabbix"),
    ("https://zbx.example.com/api_jsonrpc.php", "https://zbx.example.com"),
])
def test_frontend_base(url, expected):
    assert zs.frontend_base(url) == expected


def test_find_dashboard_id_is_case_insensitive_and_tolerates_missing():
    dashboards = [{"dashboardid": "1", "name": "Global view"}, {"dashboardid": "7", "name": "Zabbix server health"}]
    assert zs.find_dashboard_id(dashboards) == "7"
    assert zs.find_dashboard_id([{"dashboardid": "9", "name": "ZABBIX SERVER HEALTH"}]) == "9"
    assert zs.find_dashboard_id([{"dashboardid": "1", "name": "Global view"}]) is None
    assert zs.find_dashboard_id([]) is None


@pytest.mark.parametrize("text,login_form,expected_status", [
    ("Zabbix\nSign in", True, "error"),
    ("Access denied\nYou have no permissions to access this page.", False, "no_permission"),
    ("Acesso negado\nSem permissões para acessar esta página.", False, "no_permission"),
    ("Page not found", False, "error"),
    ("Unexpected server error.", False, "error"),
])
def test_invalid_page_reason_flags_non_screens(text, login_form, expected_status):
    reason = zs.invalid_page_reason(text, login_form)
    assert reason is not None and reason[0] == expected_status


def test_invalid_page_reason_accepts_real_screens_and_only_reads_the_top():
    assert zs.invalid_page_reason("Proxies\nName Mode Encryption Version", False) is None
    # a permissions-sounding phrase buried deep in a long list must not flag a valid page
    long_page = "Hosts\n" + ("host-linha " * 400) + "\naccess denied"
    assert zs.invalid_page_reason(long_page, False) is None


def test_catalog_covers_every_rule_engine_section_it_documents():
    sections = {c.section for c in zs.CATALOG}
    assert {"Arquitetura do Ambiente", "Processamento das Máquinas", "Análise de Templates",
            "Análise de Mídias", "Banco de Dados", "Análise de Dashboards", "Infraestrutura"} <= sections
    assert len({c.key for c in zs.CATALOG}) == len(zs.CATALOG)


# ------------------------------------------------------------ browser flow


def test_capture_logs_in_and_screenshots_every_valid_screen(fake_zabbix, chromium_available, tmp_path):
    results = zs.capture(
        {"url": fake_zabbix}, "admin", "secret",
        [{"dashboardid": "7", "name": "Zabbix server health"}], tmp_path,
    )
    by_key = {r["key"]: r for r in results}

    assert len(results) == len(zs.CATALOG)
    assert by_key["media_types"]["status"] == "no_permission"
    assert "Super admin" in by_key["media_types"]["message"]

    ok = [r for r in results if r["status"] == "ok"]
    assert len(ok) == len(zs.CATALOG) - 1
    for result in ok:
        image = tmp_path / result["file"]
        assert image.exists()
        with Image.open(image) as img:
            assert img.size == (1600, 900)
    # the dashboard screen resolved its id and is labelled without it
    assert by_key["server_health"]["screen"] == "zabbix.php?action=dashboard.view"


def test_capture_skips_server_health_when_dashboard_is_missing(fake_zabbix, chromium_available, tmp_path):
    results = zs.capture({"url": fake_zabbix}, "admin", "secret", [], tmp_path)
    by_key = {r["key"]: r for r in results}
    assert by_key["server_health"]["status"] == "skipped"
    assert not (tmp_path / "server_health.png").exists()


def test_capture_reports_a_clear_error_on_wrong_password(fake_zabbix, chromium_available, tmp_path):
    results = zs.capture({"url": fake_zabbix}, "admin", "wrong", [], tmp_path)
    assert len(results) == 1
    assert results[0]["status"] == "error"
    assert "login" in results[0]["message"].lower()
    assert list(tmp_path.glob("*.png")) == []
