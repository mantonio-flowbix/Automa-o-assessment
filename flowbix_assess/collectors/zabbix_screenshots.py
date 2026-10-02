"""Screenshots of the Zabbix web frontend, used as evidence slides in the PPTX.

The API token can't authenticate the web UI, so this logs in with a
frontend user (a separate credential from the API token) using a headless
Chromium. Safety model, mirroring the rest of the tool:

- The only write-shaped request is the login itself. After that it issues
  plain GET navigations to the fixed catalog below — it never clicks,
  submits or edits anything, so a Super admin account is not exercised
  beyond viewing pages.
- Each page is validated after loading (login wall, "no permissions",
  error page) and only a genuine screen is kept; failures are reported per
  page instead of silently inserting a screenshot of an error message.

Playwright is imported lazily so the rest of the package (CLI, tests, rule
engine) works on machines without a browser installed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

VIEWPORT = {"width": 1600, "height": 900}  # 16:9, same ratio as the slides
SERVER_HEALTH_DASHBOARD = "Zabbix server health"
_PAGE_TIMEOUT_MS = 30_000
_SETTLE_MS = 800


@dataclass(frozen=True)
class Capture:
    key: str
    section: str  # same names the rule engine uses for `Finding.section`
    title: str
    path: str  # relative to the frontend base; "{dashboardid}" is resolved
    needs_super_admin: bool


# Zabbix 7.0 screens that back the checks the assessment runs. Administration
# screens (and Reports > System information) require the Super admin role.
CATALOG = [
    Capture("system_info", "Arquitetura do Ambiente", "Informações do sistema",
            "zabbix.php?action=report.status", True),
    Capture("proxies", "Arquitetura do Ambiente", "Proxies: status, versão e PSK",
            "zabbix.php?action=proxy.list", True),
    Capture("server_health", "Processamento das Máquinas", "Dashboard Zabbix server health",
            "zabbix.php?action=dashboard.view&dashboardid={dashboardid}", False),
    Capture("queue", "Processamento das Máquinas", "Fila de itens (queue)",
            "zabbix.php?action=queue.overview", True),
    Capture("unsupported", "Análise de Templates", "Itens em estado unsupported",
            "zabbix.php?action=item.list&context=host&filter_state=1&filter_set=1", False),
    Capture("templates", "Análise de Templates", "Lista de templates",
            "zabbix.php?action=template.list", False),
    Capture("media_types", "Análise de Mídias", "Media types",
            "zabbix.php?action=mediatype.list", True),
    Capture("housekeeping", "Banco de Dados", "Housekeeping (retenção)",
            "zabbix.php?action=housekeeping.edit", True),
    Capture("dashboards", "Análise de Dashboards", "Lista de dashboards",
            "zabbix.php?action=dashboard.list", False),
    Capture("hosts", "Infraestrutura", "Hosts monitorados",
            "zabbix.php?action=host.view", False),
]

# Matched against the top of the page text (where Zabbix renders its message
# box), en + pt-BR since the UI language is per-user.
_NO_PERMISSION = re.compile(
    r"no permissions|you have no permissions|access denied|sem permiss|acesso negado", re.I
)
_ERROR_PAGE = re.compile(
    r"page not found|página não encontrada|fatal error|unexpected server error|"
    r"erro inesperado do servidor|you are not logged in|você não está conectado", re.I
)
_TOP_OF_PAGE_CHARS = 2000


def frontend_base(zabbix_url: str) -> str:
    """The API lives at <frontend>/api_jsonrpc.php, so the frontend base is
    the configured URL without that suffix (and without a trailing slash)."""
    url = zabbix_url.rstrip("/")
    if url.endswith("api_jsonrpc.php"):
        url = url[: -len("api_jsonrpc.php")].rstrip("/")
    return url


def find_dashboard_id(dashboards: list, name: str = SERVER_HEALTH_DASHBOARD) -> str | None:
    for dashboard in dashboards or []:
        if (dashboard.get("name") or "").strip().lower() == name.lower():
            return str(dashboard.get("dashboardid"))
    return None


def invalid_page_reason(page_text: str, has_login_form: bool):
    """Returns ("no_permission"|"error", message) when the loaded page is not
    a real screen, else None."""
    if has_login_form:
        return "error", "Sessão não autenticada (o Zabbix pediu login de novo)"
    top = (page_text or "")[:_TOP_OF_PAGE_CHARS]
    if _NO_PERMISSION.search(top):
        return "no_permission", "Sem permissão para esta tela"
    if _ERROR_PAGE.search(top):
        return "error", "O Zabbix devolveu uma página de erro"
    return None


def _result(capture: Capture, status: str, message: str = "", file: str | None = None, now=None) -> dict:
    return {
        "key": capture.key,
        "section": capture.section,
        "title": capture.title,
        "screen": capture.path.split("&dashboardid=")[0] if "{dashboardid}" in capture.path else capture.path,
        "file": file,
        "captured_at": (now or datetime.now()).isoformat(timespec="seconds"),
        "status": status,
        "message": message,
    }


def _settle(page, timeout_error) -> None:
    # Dashboards keep polling, so networkidle may never fire — best effort.
    try:
        page.wait_for_load_state("networkidle", timeout=5_000)
    except timeout_error:
        pass
    page.wait_for_timeout(_SETTLE_MS)


def _has_login_form(page) -> bool:
    return page.locator('input[name="password"]').count() > 0


def capture(zbx_config: dict, frontend_user: str, frontend_password: str,
            dashboards: list, out_dir) -> list:
    """Logs into the Zabbix frontend and screenshots every catalog screen.
    Returns one result dict per catalog entry (status "ok", "no_permission",
    "skipped" or "error"); a failed login returns a single "error" result."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = frontend_base(zbx_config["url"])
    login_probe = Capture("login", "", "Login no frontend", "index.php", False)
    results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            context = browser.new_context(
                viewport=VIEWPORT,
                ignore_https_errors=not zbx_config.get("verify_ssl", True),
            )
            page = context.new_page()
            page.set_default_timeout(_PAGE_TIMEOUT_MS)

            try:
                page.goto(f"{base}/index.php", wait_until="load")
                page.fill('input[name="name"]', frontend_user)
                page.fill('input[name="password"]', frontend_password)
                submit = page.locator('#enter, button[type="submit"], input[type="submit"]')
                if submit.count():
                    submit.first.click()
                else:
                    page.press('input[name="password"]', "Enter")
                _settle(page, PlaywrightTimeout)
            except Exception as exc:  # noqa: BLE001 — surfaced in the UI
                return [_result(login_probe, "error", f"Não foi possível abrir o frontend: {str(exc)[:160]}")]

            if _has_login_form(page):
                return [_result(login_probe, "error", "Falha no login do frontend (usuário ou senha incorretos?)")]

            for item in CATALOG:
                path = item.path
                if "{dashboardid}" in path:
                    dashboard_id = find_dashboard_id(dashboards)
                    if not dashboard_id:
                        results.append(_result(item, "skipped", f"Dashboard '{SERVER_HEALTH_DASHBOARD}' não encontrado"))
                        continue
                    path = path.format(dashboardid=dashboard_id)

                try:
                    page.goto(f"{base}/{path}", wait_until="load")
                    _settle(page, PlaywrightTimeout)
                    if not page.url.startswith(base):
                        results.append(_result(item, "error", "Redirecionou para fora do Zabbix"))
                        continue

                    problem = invalid_page_reason(page.inner_text("body"), _has_login_form(page))
                    if problem:
                        status, message = problem
                        if status == "no_permission" and item.needs_super_admin:
                            message += " (esta tela exige perfil Super admin)"
                        results.append(_result(item, status, message))
                        continue

                    filename = f"{item.key}.png"
                    page.screenshot(path=str(out_dir / filename), full_page=False)
                    results.append(_result(item, "ok", file=filename))
                except Exception as exc:  # noqa: BLE001 — one bad page must not stop the rest
                    results.append(_result(item, "error", str(exc)[:160]))
        finally:
            browser.close()

    return results
