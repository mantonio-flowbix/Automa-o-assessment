from __future__ import annotations

import re
from collections import Counter

from ..models import Finding, Severity

_DELAY_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}

# key_ pattern for Zabbix's ODBC ("Database monitor") item type; type "11" is
# the numeric item type id for the same thing, kept as a fallback in case the
# key doesn't follow the db.odbc.* convention.
_ODBC_ITEM_TYPE = "11"

# heuristic: a `)`/`]` (closing a function call over one item) followed by
# `+` followed by another function call — e.g. last(item1)+last(item2) —
# flags triggers that sum items instead of using min()/max() over a period.
_SUM_PATTERN = re.compile(r"[\)\]]\s*\+\s*(?:last|avg|min|max)\(")

_PROCESS_PARAM_HINT = {
    "poller": "StartPollers",
    "unreachable poller": "StartPollersUnreachable",
    "trapper": "StartTrappers",
    "icmp pinger": "StartPingers",
    "preprocessing worker": "StartPreprocessors",
    "history syncer": "StartDBSyncers",
    "discoverer": "StartDiscoverers",
    "http poller": "StartHTTPPollers",
    "vmware collector": "StartVMwareCollectors",
}

_PSK_TLS_VALUE = 2  # Zabbix tls_connect/tls_accept: 1=unencrypted, 2=PSK, 4=cert


def _delay_to_seconds(delay: str) -> int | None:
    if not delay:
        return None
    delay = delay.strip()
    if delay.isdigit():
        return int(delay)
    match = re.match(r"^(\d+)([smhdw])$", delay)
    if not match:
        return None
    value, unit = match.groups()
    return int(value) * _DELAY_UNIT_SECONDS[unit]


def _cpu_findings(config, zbx: dict) -> list[Finding]:
    findings = []
    warn = config.thresholds["cpu_warning_pct"]
    crit = config.thresholds["cpu_critical_pct"]
    for host, stats in zbx.get("host_cpu_stats", {}).items():
        if stats["avg"] >= crit:
            severity = Severity.CRITICAL
        elif stats["avg"] >= warn:
            severity = Severity.WARNING
        else:
            continue
        findings.append(Finding(
            section="Processamento das Máquinas",
            title=f"Utilização de CPU elevada em {host}",
            severity=severity,
            description=(
                f"Média de CPU de {stats['avg']:.1f}% (pico de {stats['max']:.1f}%) "
                f"nas últimas 24h, com base em {stats['samples']} amostras."
            ),
            recommendation=(
                "Investigar causas de picos (housekeeping, fila de pré-processamento, "
                "scripts) e avaliar upgrade de instância se a otimização não for suficiente."
            ),
            evidence=stats,
        ))
    return findings


def _unsupported_items_finding(config, zbx: dict) -> list[Finding]:
    count = zbx.get("unsupported_items_count")
    if count is None:
        return []
    count = int(count)
    threshold = config.thresholds["unsupported_items_warning_count"]
    if count == 0:
        return []
    severity = Severity.WARNING if count >= threshold else Severity.INFO
    return [Finding(
        section="Análise de Templates",
        title=f"{count} itens em estado 'unsupported'",
        severity=severity,
        description="Itens retornando erro (timeout, valor vazio, erro de agente, etc).",
        recommendation=(
            "Configurar pré-processamento para tratar erros/valores vazios; para itens "
            "system.run, revisar configuração do agente no host de origem."
        ),
        evidence={"count": count},
    )]


def _master_item_history_findings(config, zbx: dict) -> list[Finding]:
    findings = []
    for tpl in zbx.get("templates", []):
        items = tpl.get("items", [])
        master_ids = {i["master_itemid"] for i in items if i.get("master_itemid") not in (None, "0")}
        offending = []
        for item in items:
            if item["itemid"] not in master_ids:
                continue
            if item.get("history", "0") in ("0", "0d", ""):
                continue
            dependents_with_history = [
                d for d in items
                if d.get("master_itemid") == item["itemid"] and d.get("history", "0") not in ("0", "0d", "")
            ]
            if dependents_with_history:
                offending.append(item["name"])
        if offending:
            findings.append(Finding(
                section="Análise de Templates",
                title=f"Itens master salvando histórico duplicado em '{tpl['name']}'",
                severity=Severity.WARNING,
                description=(
                    f"{len(offending)} item(ns) master mantêm histórico habilitado apesar de "
                    f"seus itens dependentes também armazenarem: {', '.join(offending[:10])}"
                    + (", ..." if len(offending) > 10 else "")
                ),
                recommendation="Desabilitar histórico nos itens master; manter apenas nos dependentes.",
                evidence={"template": tpl["name"], "items": offending},
                estimated_effort_hours=1.5,
            ))
    return findings


def _short_interval_findings(config, zbx: dict) -> list[Finding]:
    """General check: any item collecting faster than the threshold,
    regardless of whether it has a trigger. See also
    `_short_interval_no_trigger_findings` for the lower-priority subset."""
    findings = []
    threshold = config.thresholds["short_interval_seconds"]
    for tpl in zbx.get("templates", []):
        offending = []
        for item in tpl.get("items", []):
            seconds = _delay_to_seconds(item.get("delay", ""))
            if seconds is None or seconds >= threshold:
                continue
            offending.append(item["name"])
        if offending:
            findings.append(Finding(
                section="Análise de Templates",
                title=f"Itens com intervalo de coleta curto em '{tpl['name']}'",
                severity=Severity.INFO,
                description=(
                    f"{len(offending)} item(ns) com coleta abaixo de {threshold}s: "
                    f"{', '.join(offending[:10])}" + (", ..." if len(offending) > 10 else "")
                ),
                recommendation=(
                    "Usar intervalos mais longos quando não houver necessidade específica de "
                    "monitoramento em tempo real — diminui a carga no banco e no servidor."
                ),
                evidence={"template": tpl["name"], "items": offending},
            ))
    return findings


def _short_interval_no_trigger_findings(config, zbx: dict) -> list[Finding]:
    """Lower-priority subset of the above: short interval AND no trigger
    depends on that timing, so it's the safest place to relax the interval
    first."""
    findings = []
    threshold = config.thresholds["short_interval_seconds"]
    for tpl in zbx.get("templates", []):
        offending = []
        for item in tpl.get("items", []):
            seconds = _delay_to_seconds(item.get("delay", ""))
            if seconds is None or seconds >= threshold:
                continue
            if item.get("triggers"):
                continue
            offending.append(item["name"])
        if offending:
            findings.append(Finding(
                section="Análise de Templates",
                title=f"Itens de baixa prioridade com intervalo curto e sem trigger em '{tpl['name']}'",
                severity=Severity.INFO,
                description=(
                    f"{len(offending)} item(ns) com coleta abaixo de {threshold}s e sem "
                    f"nenhuma trigger associada — indica menor criticidade: "
                    f"{', '.join(offending[:10])}" + (", ..." if len(offending) > 10 else "")
                ),
                recommendation="Aumentar o intervalo de coleta desses itens, salvo necessidade específica.",
                evidence={"template": tpl["name"], "items": offending},
            ))
    return findings


def _odbc_item_findings(config, zbx: dict) -> list[Finding]:
    findings = []
    for tpl in zbx.get("templates", []):
        offending = []
        for item in tpl.get("items", []):
            is_odbc = item.get("type") == _ODBC_ITEM_TYPE or item.get("key_", "").startswith("db.odbc")
            if is_odbc:
                offending.append((item["name"], item.get("error") or ""))
        if not offending:
            continue
        with_errors = [name for name, err in offending if err]
        severity = Severity.WARNING if with_errors else Severity.INFO
        description = (
            f"{len(offending)} item(ns) fazem conexão direta ao banco via ODBC: "
            f"{', '.join(name for name, _ in offending[:10])}."
        )
        if with_errors:
            description += (
                f" {len(with_errors)} com erro de conexão reportado "
                f"(ex.: limite de conexões do banco excedido)."
            )
        findings.append(Finding(
            section="Análise de Templates",
            title=f"Itens com conexão direta ao banco via ODBC em '{tpl['name']}'",
            severity=severity,
            description=description,
            recommendation=(
                "Usar abordagem de item master com itens dependentes para reduzir a "
                "quantidade de conexões diretas ao banco."
            ),
            evidence={"template": tpl["name"], "items": offending},
        ))
    return findings


def _trigger_logic_findings(config, zbx: dict) -> list[Finding]:
    findings = []
    for tpl in zbx.get("templates", []):
        offending = [
            trig.get("description") or trig.get("triggerid")
            for trig in tpl.get("triggers", [])
            if _SUM_PATTERN.search(trig.get("expression", ""))
        ]
        if offending:
            findings.append(Finding(
                section="Análise de Templates",
                title=f"Triggers somando valores de itens em '{tpl['name']}'",
                severity=Severity.INFO,
                description=(
                    f"{len(offending)} trigger(s) parecem somar valores de múltiplas funções "
                    f"de item (ex.: last(a)+last(b)): {', '.join(str(t) for t in offending[:10])}."
                ),
                recommendation=(
                    "Avaliar substituir a soma por min()/max() sobre um intervalo de tempo, "
                    "quando aplicável — simplifica manutenção e o entendimento da lógica."
                ),
                evidence={"template": tpl["name"], "triggers": offending},
            ))
    return findings


def _unsupported_items_causes_findings(config, zbx: dict) -> list[Finding]:
    sample = zbx.get("unsupported_items_sample") or []
    if not sample:
        return []
    causes = Counter()
    for item in sample:
        error = (item.get("error") or "").lower()
        key = item.get("key_", "")
        if key.startswith("system.run"):
            causes["configuração do agente (system.run)"] += 1
        elif "timeout" in error:
            causes["timeout"] += 1
        elif "odbc" in error or "sql" in error:
            causes["erro odbc/sql"] += 1
        elif "empty" in error or "vazio" in error:
            causes["valor vazio"] += 1
        elif not error:
            causes["sem mensagem de erro"] += 1
        else:
            causes["outros"] += 1
    breakdown = ", ".join(f"{cause}: {count}" for cause, count in causes.most_common())
    return [Finding(
        section="Análise de Templates",
        title="Causas dos itens 'unsupported' (amostra)",
        severity=Severity.INFO,
        description=f"Com base em uma amostra de {len(sample)} itens: {breakdown}.",
        recommendation="Priorizar a causa mais frequente primeiro (normalmente timeout ou valor vazio).",
        evidence={"sample_size": len(sample), "causes": dict(causes)},
    )]


def _process_busy_findings(config, zbx: dict) -> list[Finding]:
    findings = []
    warn = config.thresholds["process_busy_warning_pct"]
    crit = config.thresholds["process_busy_critical_pct"]
    for host, procs in zbx.get("process_busy_stats", {}).items():
        offending = [(proc, busy) for proc, busy in procs.items() if busy >= warn]
        if not offending:
            continue
        worst = max(busy for _, busy in offending)
        severity = Severity.CRITICAL if worst >= crit else Severity.WARNING
        details = ", ".join(f"{proc} ({busy}%)" for proc, busy in offending)
        params = ", ".join(sorted({_PROCESS_PARAM_HINT.get(proc, proc) for proc, _ in offending}))
        findings.append(Finding(
            section="Processamento das Máquinas",
            title=f"Processos internos do Zabbix sobrecarregados em '{host}'",
            severity=severity,
            description=f"Processos acima de {warn}% de utilização: {details}.",
            recommendation=f"Aumentar o(s) parâmetro(s) {params} no zabbix_server.conf/zabbix_proxy.conf.",
            evidence={"host": host, "processes": procs},
        ))
    return findings


def _architecture_summary_finding(config, zbx: dict) -> list[Finding]:
    if not zbx.get("version"):
        return []
    proxies = zbx.get("proxies") or []
    proxy_names = [p.get("name") or p.get("host") for p in proxies]
    return [Finding(
        section="Arquitetura do Ambiente",
        title="Topologia do ambiente Zabbix",
        severity=Severity.INFO,
        description=(
            f"Zabbix Server versão {zbx.get('version')}, com {len(proxies)} proxy(s): "
            f"{', '.join(proxy_names) if proxy_names else 'nenhum'}."
        ),
        recommendation="Confirmar se a topologia documentada bate com o ambiente real (server, proxies, banco).",
        evidence={"version": zbx.get("version"), "proxies": proxy_names},
    )]


def _proxy_psk_findings(config, zbx: dict) -> list[Finding]:
    offending = []
    for proxy in zbx.get("proxies", []):
        tls_connect = proxy.get("tls_connect")
        if tls_connect is None:
            continue
        if int(tls_connect) != _PSK_TLS_VALUE:
            offending.append(proxy.get("name") or proxy.get("host"))
    if not offending:
        return []
    return [Finding(
        section="Arquitetura do Ambiente",
        title="Proxies sem criptografia PSK configurada na comunicação com o server",
        severity=Severity.WARNING,
        description=f"Proxies com tls_connect diferente de PSK: {', '.join(offending)}.",
        recommendation="Configurar criptografia PSK na comunicação proxy → servidor.",
        evidence={"proxies": offending},
    )]


def _access_policy_manual_review(config, zbx: dict) -> list[Finding]:
    return [Finding(
        section="Arquitetura do Ambiente",
        title="Forma de acesso ao ambiente (SSH/Web via VPN/cofre de senhas)",
        severity=Severity.MANUAL_REVIEW,
        description=(
            "Este MVP não valida automaticamente a política de acesso (uso de VPN, "
            "cofre de senhas) — é uma configuração de rede/processo, não algo exposto "
            "pela API do Zabbix."
        ),
        recommendation="Confirmar manualmente com o time de segurança/infraestrutura do cliente.",
        evidence={},
    )]


def _excessive_preprocessing_findings(config, zbx: dict) -> list[Finding]:
    findings = []
    threshold = config.thresholds["max_preprocessing_steps"]
    for tpl in zbx.get("templates", []):
        offending = [
            (item["name"], len(item.get("preprocessing", [])))
            for item in tpl.get("items", [])
            if len(item.get("preprocessing", [])) > threshold
        ]
        if offending:
            details = ", ".join(f"{name} ({count} passos)" for name, count in offending[:10])
            findings.append(Finding(
                section="Análise de Templates",
                title=f"Pré-processamento excessivo em '{tpl['name']}'",
                severity=Severity.WARNING,
                description=f"{len(offending)} item(ns) acima de {threshold} passos: {details}",
                recommendation=(
                    "Consolidar passos de pré-processamento (ex.: usar um único script "
                    "JavaScript no lugar de múltiplos passos encadeados)."
                ),
                evidence={"template": tpl["name"], "items": offending},
                estimated_effort_hours=len(offending) * 2.0,
            ))
    return findings


def _excessive_discovery_findings(config, zbx: dict) -> list[Finding]:
    findings = []
    threshold = config.thresholds["max_discovery_rules_per_template"]
    for tpl in zbx.get("templates", []):
        rules = tpl.get("discovery_rules", [])
        if len(rules) > threshold:
            findings.append(Finding(
                section="Análise de Templates",
                title=f"Quantidade elevada de discoveries em '{tpl['name']}'",
                severity=Severity.INFO,
                description=f"{len(rules)} discovery rules (limite sugerido: {threshold}).",
                recommendation="Avaliar uso de overrides para reduzir a quantidade de discoveries.",
                evidence={"template": tpl["name"], "count": len(rules)},
            ))
    return findings


def _media_type_findings(config, zbx: dict) -> list[Finding]:
    findings = []
    flagged_names = [n.lower() for n in config.thresholds.get("media_types_deprecated_js", ["servicenow"])]
    for mt in zbx.get("media_types", []):
        if mt.get("name", "").lower() in flagged_names:
            findings.append(Finding(
                section="Análise de Mídias",
                title=f"Media type '{mt['name']}' pode usar objetos JS deprecados",
                severity=Severity.WARNING,
                description=(
                    "Media types baseados em script/webhook que usam objetos JavaScript "
                    "descontinuados a partir da versão 7.0 do Zabbix."
                ),
                recommendation=f"Revisar e ajustar o script antes de atualizar para {config.target_zabbix_version}.",
                evidence={"media_type": mt["name"]},
            ))
    return findings


def _dashboard_naming_findings(config, zbx: dict) -> list[Finding]:
    findings = []
    suspects = [d["name"] for d in zbx.get("dashboards", []) if re.search(r"\bteste?\b", d["name"], re.I)]
    if suspects:
        findings.append(Finding(
            section="Análise de Dashboards",
            title="Dashboards com nome sugestivo de teste",
            severity=Severity.MANUAL_REVIEW,
            description=f"Dashboards candidatos a remoção: {', '.join(suspects)}",
            recommendation="Confirmar se ainda estão em uso; remover se não estiverem.",
            evidence={"dashboards": suspects},
        ))
    return findings


def _housekeeper_findings(config, zbx: dict) -> list[Finding]:
    hk = zbx.get("housekeeping")
    if not hk:
        return []
    findings = []
    if hk.get("hk_history_global") == "0":
        findings.append(Finding(
            section="Banco de Dados",
            title="Retenção de histórico configurada por item (não global)",
            severity=Severity.MANUAL_REVIEW,
            description=(
                "hk_history_global está desabilitado — a limpeza depende da configuração "
                "individual de cada item, o que historicamente leva a dados muito antigos "
                "não removidos em tabelas grandes."
            ),
            recommendation="Revisar overrides de retenção por item ou habilitar retenção global consistente.",
            evidence=hk,
        ))
    return findings


def evaluate(config, zbx: dict) -> list[Finding]:
    findings = []
    findings += _architecture_summary_finding(config, zbx)
    findings += _proxy_psk_findings(config, zbx)
    findings += _access_policy_manual_review(config, zbx)
    findings += _cpu_findings(config, zbx)
    findings += _process_busy_findings(config, zbx)
    findings += _unsupported_items_finding(config, zbx)
    findings += _unsupported_items_causes_findings(config, zbx)
    findings += _master_item_history_findings(config, zbx)
    findings += _short_interval_findings(config, zbx)
    findings += _short_interval_no_trigger_findings(config, zbx)
    findings += _odbc_item_findings(config, zbx)
    findings += _excessive_preprocessing_findings(config, zbx)
    findings += _excessive_discovery_findings(config, zbx)
    findings += _trigger_logic_findings(config, zbx)
    findings += _media_type_findings(config, zbx)
    findings += _dashboard_naming_findings(config, zbx)
    findings += _housekeeper_findings(config, zbx)
    return findings
