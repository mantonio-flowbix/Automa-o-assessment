from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .models import Severity

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"

SEVERITY_LABELS = {
    Severity.CRITICAL: "Crítico",
    Severity.WARNING: "Atenção",
    Severity.INFO: "Informativo",
    Severity.MANUAL_REVIEW: "Revisão Manual",
}


def render(config, findings, output_path: str, errors: dict | None = None):
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("report_template.html")

    by_section = defaultdict(list)
    for finding in findings:
        by_section[finding.section].append(finding)

    counts = defaultdict(int)
    for finding in findings:
        counts[finding.severity] += 1

    html = template.render(
        client_name=config.client_name,
        target_zabbix_version=config.target_zabbix_version,
        generated_at=datetime.now().strftime("%d/%m/%Y %H:%M"),
        by_section=dict(by_section),
        counts=counts,
        severity_labels=SEVERITY_LABELS,
        errors=errors or {},
        total_findings=len(findings),
    )

    Path(output_path).write_text(html, encoding="utf-8")
    return output_path
