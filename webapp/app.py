"""Central web front-end for flowbix-assess.

Flow per client: enter Zabbix/MySQL/Grafana credentials once (URL and
thresholds saved to data/clients/<slug>/config.yaml; tokens/passwords saved
to that client's local .env — see clientstore.ClientStore), download the
probe script for that client and run it manually via SSH on the client's
terminal (this server never gets SSH access to client environments), then
upload the resulting .json here. Hitting "Rodar validação" calls one
endpoint per source in sequence (/run/zabbix, /run/mysql, /run/infra,
/run/report) so the UI can show each step completing instead of a single
opaque spinner. Each step is independent and best-effort: a failure in one
doesn't block the others, mirroring how the CLI already tolerates partial
sources.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from flowbix_assess.clientstore import ClientStore, slugify
from flowbix_assess.collectors import infra_local
from flowbix_assess.collectors import mysql_collector
from flowbix_assess.collectors import zabbix as zabbix_collector
from flowbix_assess.config import Config
from flowbix_assess.models import CollectionResult
from flowbix_assess.pptx_report import render as render_pptx
from flowbix_assess.report import render as render_report
from flowbix_assess.rules import run_all

app = Flask(__name__)

PROBE_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "probe" / "flowbix_probe.py"

STEP_LABELS = {
    "zabbix": "Zabbix API",
    "mysql": "MySQL",
    "infra": "Infraestrutura",
    "report": "Relatório final",
}


def _build_raw_config(form, existing: dict) -> tuple[dict, dict]:
    """Splits the submitted form into (config, secrets).

    `config` only ever holds `${VAR}` placeholders for credentials — it's
    what gets written to `config.yaml` and is safe to back up or version.
    `secrets` holds the real values, written to the client's local `.env`
    (see `ClientStore.save_env`) and never rendered back into HTML or
    stored in `config.yaml`. Leaving a credential field blank in the form
    keeps whatever was already saved (`ClientStore.save_env` merges rather
    than overwrites), so `secrets` only needs to carry non-blank values.
    """
    infra_hosts = []
    for line in (form.get("infra_hosts") or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        name = parts[0]
        role = parts[1] if len(parts) > 1 and parts[1] else None
        infra_hosts.append({"name": name, "role": role})

    raw = {
        "client": {
            "name": form.get("client_name") or existing.get("client", {}).get("name", ""),
            "target_zabbix_version": form.get("target_zabbix_version") or "7.0",
            "hosting_type": form.get("hosting_type") or "",
        },
        "infra": {"hosts": infra_hosts},
    }
    secrets: dict = {}

    zabbix_url = form.get("zabbix_url")
    if zabbix_url:
        raw["zabbix"] = {
            "url": zabbix_url,
            "token": "${ZABBIX_TOKEN}",
            "token_expires_at": form.get("zabbix_token_expires_at") or "",
            "verify_ssl": True,
            "hosts_of_interest": [h["name"] for h in infra_hosts],
        }
        secrets["ZABBIX_TOKEN"] = form.get("zabbix_token") or ""
        if form.get("zabbix_template_limit"):
            raw["zabbix"]["template_limit"] = int(form["zabbix_template_limit"])

    mysql_host = form.get("mysql_host")
    if mysql_host:
        raw["mysql"] = {
            "host": mysql_host,
            "port": int(form.get("mysql_port") or 3306),
            "user": form.get("mysql_user") or "",
            "password": "${MYSQL_PASSWORD}",
            "database": form.get("mysql_database") or "zabbix",
        }
        secrets["MYSQL_PASSWORD"] = form.get("mysql_password") or ""

    grafana_url = form.get("grafana_url")
    if grafana_url:
        raw["grafana"] = {
            "url": grafana_url,
            "api_key": "${GRAFANA_TOKEN}",
            "token_expires_at": form.get("grafana_token_expires_at") or "",
            "verify_ssl": True,
        }
        secrets["GRAFANA_TOKEN"] = form.get("grafana_token") or ""

    return raw, secrets


def _token_expired(source_cfg: dict | None) -> str | None:
    """Returns the expiry date string if `source_cfg.token_expires_at` has
    already passed, else None. Date-only (YYYY-MM-DD) — a token expiring
    "today" is still treated as valid for the rest of that day."""
    if not source_cfg:
        return None
    expires_at = source_cfg.get("token_expires_at")
    if not expires_at:
        return None
    try:
        expiry = datetime.date.fromisoformat(expires_at)
    except ValueError:
        return None
    if expiry < datetime.date.today():
        return expires_at
    return None


@app.route("/")
def index():
    clients = ClientStore.list_all()
    return render_template("index.html", clients=clients)


@app.route("/clients/new", methods=["POST"])
def create_client():
    name = request.form.get("client_name", "").strip()
    if not name:
        return redirect(url_for("index"))
    slug = slugify(name)
    store = ClientStore(slug)
    store.ensure()
    if not store.config_path.exists():
        store.save_config({
            "client": {"name": name, "target_zabbix_version": "7.0"},
            "infra": {"hosts": []},
        })
    return redirect(url_for("client_page", slug=slug))


@app.route("/clients/<slug>")
def client_page(slug):
    store = ClientStore(slug)
    store.ensure()
    raw = store.load_config_raw()
    env = store.load_env()
    infra_files = [p.name for p in store.infra_files()]
    reports = [p.name for p in store.list_reports()]
    return render_template(
        "client.html",
        slug=slug,
        raw=raw,
        infra_files=infra_files,
        reports=reports,
        has_zabbix_token=bool(env.get("ZABBIX_TOKEN")),
        has_mysql_password=bool(env.get("MYSQL_PASSWORD")),
        has_grafana_token=bool(env.get("GRAFANA_TOKEN")),
        zabbix_token_expired=_token_expired(raw.get("zabbix")),
        grafana_token_expired=_token_expired(raw.get("grafana")),
    )


@app.route("/clients/<slug>/config", methods=["POST"])
def save_client_config(slug):
    store = ClientStore(slug)
    existing = store.load_config_raw()
    raw, secrets = _build_raw_config(request.form, existing)
    store.save_config(raw)
    store.save_env(secrets)
    return jsonify({"status": "ok"})


@app.route("/clients/<slug>/probe-script")
def download_probe_script(slug):
    script = PROBE_SCRIPT_PATH.read_text(encoding="utf-8")
    return Response(
        script,
        mimetype="text/x-python",
        headers={
            "Content-Disposition": f"attachment; filename=flowbix_probe_{slug}.py"
        },
    )


@app.route("/clients/<slug>/infra/upload", methods=["POST"])
def upload_infra_file(slug):
    store = ClientStore(slug)
    store.ensure()
    uploaded = request.files.getlist("infra_files")
    if not uploaded:
        return jsonify({"status": "error", "message": "Nenhum arquivo enviado"}), 400

    saved, errors = [], []
    for file in uploaded:
        name = Path(file.filename or "").name  # strip any path, keep basename only
        if not name.lower().endswith(".json"):
            errors.append(f"{name}: precisa ser .json")
            continue
        try:
            payload = json.loads(file.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"{name}: JSON inválido ({exc})")
            continue
        if "host" not in payload:
            errors.append(f"{name}: sem campo \"host\" — não parece uma saída do probe")
            continue
        (store.infra_dir / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        saved.append(name)

    if not saved:
        return jsonify({"status": "error", "message": "; ".join(errors)}), 400
    status = "ok" if not errors else "partial"
    return jsonify({"status": status, "saved": saved, "errors": errors})


@app.route("/clients/<slug>/run/<step>", methods=["POST"])
def run_step(slug, step):
    store = ClientStore(slug)
    raw = store.load_config_raw()
    config = Config.from_raw(raw, extra_env=store.load_env())

    try:
        if step == "zabbix":
            if not config.zabbix:
                return jsonify({"status": "skipped", "message": "Zabbix não configurado"})
            expired = _token_expired(config.zabbix)
            if expired:
                return jsonify({
                    "status": "skipped",
                    "message": f"Token expirado em {expired} — atualize antes de rodar",
                })
            data = zabbix_collector.collect(
                config.zabbix,
                hosts_of_interest=config.zabbix.get("hosts_of_interest", []),
                template_limit=config.zabbix.get("template_limit"),
            )
            store.save_run_artifact("zabbix", data)
            return jsonify({"status": "ok", "summary": {
                "versão": data.get("version"),
                "templates": len(data.get("templates", [])),
                "itens_unsupported": data.get("unsupported_items_count"),
            }})

        if step == "mysql":
            if not config.mysql:
                return jsonify({"status": "skipped", "message": "MySQL não configurado"})
            data = mysql_collector.collect(config.mysql)
            store.save_run_artifact("mysql", data)
            total_gb = sum(
                t["data_length"] + t["index_length"] for t in data.get("tables", [])
            ) / (1024 ** 3)
            store.append_size_history(round(total_gb, 2))
            return jsonify({"status": "ok", "summary": {
                "versão": data.get("version"),
                "tamanho_gb": round(total_gb, 2),
            }})

        if step == "infra":
            files = store.infra_files()
            if not files:
                return jsonify({"status": "skipped", "message": "Nenhum arquivo em infra/"})
            data = infra_local.collect(str(store.infra_dir))
            store.save_run_artifact("infra", data)
            return jsonify({"status": "ok", "summary": {
                "hosts": list(data.get("hosts", {}).keys()),
            }})

        if step == "report":
            collection = CollectionResult(
                zabbix=store.load_run_artifact("zabbix"),
                mysql=store.load_run_artifact("mysql"),
                infra=store.load_run_artifact("infra"),
                grafana={},
                mysql_size_history=store.load_size_history(),
            )
            findings = run_all(config, collection)
            timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            report_path = store.reports_dir / f"report-{timestamp}.html"
            render_report(config, findings, str(report_path))

            pptx_path = store.reports_dir / f"apresentacao-{timestamp}.pptx"
            render_pptx(config, findings, str(pptx_path))

            return jsonify({
                "status": "ok",
                "summary": {"total_achados": len(findings)},
                "report_url": url_for("serve_report", slug=slug, filename=report_path.name),
                "pptx_url": url_for("serve_report", slug=slug, filename=pptx_path.name),
            })

        return jsonify({"status": "error", "message": f"Etapa desconhecida: {step}"}), 400

    except Exception as exc:  # noqa: BLE001 — surfaced to the UI, not swallowed
        return jsonify({"status": "error", "message": str(exc)})


@app.route("/clients/<slug>/reports/<path:filename>")
def serve_report(slug, filename):
    store = ClientStore(slug)
    return send_from_directory(store.reports_dir, filename)


def main():
    # 0.0.0.0 so the container is reachable from the Caddy reverse proxy —
    # what's actually exposed to the outside is decided by docker-compose's
    # port mapping / the VM's firewall, not this bind address. debug=True
    # would let anyone who reaches this port execute arbitrary code via the
    # Werkzeug debugger, unacceptable now that this runs on a shared,
    # network-reachable server — opt in explicitly for local dev only.
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=5050, debug=debug)


if __name__ == "__main__":
    main()
