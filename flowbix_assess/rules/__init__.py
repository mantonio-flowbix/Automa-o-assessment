from __future__ import annotations

from ..models import Finding, SEVERITY_ORDER
from . import zabbix_rules, database_rules, grafana_rules, infra_rules


def run_all(config, collection) -> list[Finding]:
    findings: list[Finding] = []

    if collection.zabbix:
        findings += zabbix_rules.evaluate(config, collection.zabbix)
    if collection.mysql:
        findings += database_rules.evaluate(
            config, collection.mysql, collection.zabbix, collection.mysql_size_history
        )
    if collection.infra:
        findings += infra_rules.evaluate(config, collection.infra, collection.zabbix)
    if collection.grafana:
        findings += grafana_rules.evaluate(config, collection.grafana)

    findings.sort(key=lambda f: SEVERITY_ORDER[f.severity])
    return findings
