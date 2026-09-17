from __future__ import annotations

import re

from ..models import Finding, Severity


def _version_tuple(version: str):
    parts = re.findall(r"\d+", version or "")
    return tuple(int(p) for p in parts[:3]) if parts else (0,)


def _version_finding(config, grafana: dict) -> list[Finding]:
    current = grafana.get("version")
    latest_known = config.thresholds.get("grafana_latest_known_version")
    if not current or not latest_known:
        return []
    if _version_tuple(current) >= _version_tuple(latest_known):
        return []
    return [Finding(
        section="Grafana",
        title=f"Grafana desatualizado ({current})",
        severity=Severity.WARNING,
        description=f"Versão em uso ({current}) está atrás da versão mais recente conhecida ({latest_known}).",
        recommendation="Planejar atualização para aproveitar correções de segurança e performance.",
        evidence={"current": current, "latest_known": latest_known},
    )]


def _backend_db_finding(config, grafana: dict) -> list[Finding]:
    backend = grafana.get("backend_db_type")
    if backend is None:
        return [Finding(
            section="Grafana",
            title="Backend de dados do Grafana não verificado",
            severity=Severity.MANUAL_REVIEW,
            description=(
                "A API do Grafana não expõe o tipo de banco usado internamente "
                "(SQLite vs MySQL/MariaDB) — requer acesso ao grafana.ini."
            ),
            recommendation="Preencher 'grafana.backend_db_type' no config manualmente, ou usar coletor SSH (roadmap v2).",
            evidence={},
        )]
    if backend.lower() in ("sqlite3", "sqlite"):
        return [Finding(
            section="Grafana",
            title="Grafana usando SQLite como backend",
            severity=Severity.WARNING,
            description="SQLite não é recomendado para ambientes com múltiplos usuários simultâneos.",
            recommendation="Migrar para MariaDB ou MySQL.",
            evidence={"backend_db_type": backend},
        )]
    return []


def _dashboard_naming_findings(config, grafana: dict) -> list[Finding]:
    suspects = [d["title"] for d in grafana.get("dashboards", []) if re.search(r"\bteste?\b", d.get("title", ""), re.I)]
    if not suspects:
        return []
    return [Finding(
        section="Grafana",
        title="Dashboards com nome sugestivo de teste/sem uso",
        severity=Severity.MANUAL_REVIEW,
        description=f"Candidatos a revisão/remoção: {', '.join(suspects)}",
        recommendation="Confirmar uso real e remover dashboards obsoletos para reduzir ruído.",
        evidence={"dashboards": suspects},
    )]


def _deep_dashboard_review_note(config, grafana: dict) -> list[Finding]:
    count = len(grafana.get("dashboards", []))
    if count == 0:
        return []
    return [Finding(
        section="Grafana",
        title="Revisão de queries e volume de rows por dashboard",
        severity=Severity.MANUAL_REVIEW,
        description=(
            f"{count} dashboard(s) encontrados. Este MVP não baixa o JSON model de cada "
            f"dashboard, então não detecta automaticamente queries sem filtro de tempo "
            f"nem excesso de rows/painéis."
        ),
        recommendation="Roadmap v2: buscar /api/dashboards/uid/{uid} e inspecionar o JSON model.",
        evidence={"dashboard_count": count},
    )]


def evaluate(config, grafana: dict) -> list[Finding]:
    findings = []
    findings += _version_finding(config, grafana)
    findings += _backend_db_finding(config, grafana)
    findings += _dashboard_naming_findings(config, grafana)
    findings += _deep_dashboard_review_note(config, grafana)
    return findings
