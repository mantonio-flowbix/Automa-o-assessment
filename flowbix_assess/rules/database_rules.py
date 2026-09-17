from __future__ import annotations

from datetime import date, datetime

from ..models import Finding, Severity

HISTORY_TABLES = ["history", "history_uint", "history_str", "history_text", "history_log"]


def _parse_version_prefix(version: str) -> str:
    parts = version.split(".")
    return ".".join(parts[:2])


def _mysql_eol_finding(config, mysql: dict) -> list[Finding]:
    version = mysql.get("version", "")
    prefix = _parse_version_prefix(version)
    eol_map = config.thresholds.get("mysql_eol_versions", {})
    eol_str = eol_map.get(prefix)
    if not eol_str:
        return []
    eol_date = datetime.strptime(eol_str, "%Y-%m-%d").date()
    days_left = (eol_date - date.today()).days
    if days_left > 365:
        return []
    severity = Severity.CRITICAL if days_left <= 0 else Severity.WARNING
    return [Finding(
        section="Banco de Dados",
        title=f"MySQL {version} próximo do fim de suporte",
        severity=severity,
        description=(
            f"Versão {version} entra em fim de suporte da comunidade em {eol_str} "
            f"({'já passou' if days_left <= 0 else f'em {days_left} dias'})."
        ),
        recommendation="Planejar atualização para uma versão com suporte ativo (ex.: 8.4 LTS).",
        evidence={"version": version, "eol": eol_str},
    )]


def _partitioning_findings(config, mysql: dict) -> list[Finding]:
    tables = {t["table_name"]: t for t in mysql.get("tables", [])}
    partitioned = set(mysql.get("partitioned_tables", []))
    threshold_gb = config.thresholds["history_table_size_gb_warning"]

    offending = []
    for name in HISTORY_TABLES:
        table = tables.get(name)
        if not table or name in partitioned:
            continue
        size_gb = (table["data_length"] + table["index_length"]) / (1024 ** 3)
        if size_gb >= 1:
            offending.append((name, round(size_gb, 2)))

    if not offending:
        return []

    total_gb = sum(size for _, size in offending)
    severity = Severity.CRITICAL if total_gb >= threshold_gb else Severity.WARNING
    details = ", ".join(f"{name} ({size} GB)" for name, size in offending)
    return [Finding(
        section="Banco de Dados",
        title="Tabelas de histórico sem particionamento",
        severity=severity,
        description=f"Tabelas sem partição encontradas: {details}.",
        recommendation=(
            "Habilitar event scheduler e aplicar particionamento nas tabelas de histórico; "
            "planejar limpeza de dados antigos não utilizados."
        ),
        evidence={"tables": offending, "total_gb": round(total_gb, 2)},
    )]


def _db_size_info(config, mysql: dict) -> list[Finding]:
    tables = mysql.get("tables", [])
    if not tables:
        return []
    total_gb = sum(t["data_length"] + t["index_length"] for t in tables) / (1024 ** 3)
    return [Finding(
        section="Banco de Dados",
        title="Tamanho total do banco de dados",
        severity=Severity.INFO,
        description=f"Tamanho aproximado: {total_gb:.2f} GB em {len(tables)} tabelas.",
        recommendation="Acompanhar crescimento mensal para dimensionar armazenamento e retenção.",
        evidence={"total_gb": round(total_gb, 2)},
    )]


def _history_age_finding(config, mysql: dict, zbx: dict) -> list[Finding]:
    oldest = mysql.get("history_oldest_clock")
    if not oldest:
        return []
    hk = (zbx or {}).get("housekeeping") or {}
    configured_days = hk.get("hk_history")
    findings = []
    for table, clock in oldest.items():
        if not clock:
            continue
        age_days = (datetime.now().timestamp() - clock) / 86400
        if configured_days and configured_days.isdigit() and age_days > int(configured_days) * 2:
            findings.append(Finding(
                section="Banco de Dados",
                title=f"Dados muito antigos em '{table}'",
                severity=Severity.WARNING,
                description=(
                    f"Registro mais antigo tem {age_days:.0f} dias, bem acima da retenção "
                    f"configurada ({configured_days} dias). Indica housekeeper ineficiente "
                    f"para esta tabela."
                ),
                recommendation="Investigar housekeeping e considerar limpeza manual + particionamento.",
                evidence={"table": table, "age_days": round(age_days, 1)},
            ))
    return findings


def _zabbix_compat_finding(config, mysql: dict) -> list[Finding]:
    version = mysql.get("version", "")
    prefix = _parse_version_prefix(version)
    if not prefix:
        return []
    compat = config.thresholds.get("zabbix_mysql_compat", {})
    required = compat.get(config.target_zabbix_version)
    if not required:
        return []

    def _tuple(v):
        return tuple(int(p) for p in v.split("."))

    if _tuple(prefix) >= _tuple(required):
        return []
    return [Finding(
        section="Banco de Dados",
        title=f"MySQL {version} abaixo do mínimo exigido pelo Zabbix {config.target_zabbix_version}",
        severity=Severity.CRITICAL,
        description=(
            f"Zabbix {config.target_zabbix_version} requer MySQL {required}+; "
            f"o ambiente está em {version}."
        ),
        recommendation=(
            f"Atualizar o MySQL para {required} ou superior antes de migrar para "
            f"o Zabbix {config.target_zabbix_version}."
        ),
        evidence={"version": version, "required": required, "target_zabbix": config.target_zabbix_version},
    )]


def _tuning_findings(config, mysql: dict) -> list[Finding]:
    variables = mysql.get("variables") or {}
    if not variables:
        return []

    issues = []
    event_scheduler = (variables.get("event_scheduler") or "").upper()
    if event_scheduler == "OFF":
        issues.append("event_scheduler está OFF (necessário para agendar particionamento automático)")

    max_conn = variables.get("max_connections")
    min_recommended = config.thresholds["mysql_min_connections_recommended"]
    if max_conn and str(max_conn).isdigit() and int(max_conn) < min_recommended:
        issues.append(f"max_connections em {max_conn}, abaixo do mínimo recomendado ({min_recommended})")

    severity = Severity.WARNING if issues else Severity.INFO
    description = "; ".join(issues) if issues else "Sem desvios identificados nos parâmetros observados."
    description += (
        f" Valores atuais — max_connections: {variables.get('max_connections', 'n/d')}, "
        f"event_scheduler: {variables.get('event_scheduler', 'n/d')}, "
        f"innodb_io_capacity: {variables.get('innodb_io_capacity', 'n/d')}."
    )
    return [Finding(
        section="Banco de Dados",
        title="Parâmetros de tuning do banco de dados",
        severity=severity,
        description=description,
        recommendation=(
            "Habilitar event_scheduler se for implementar particionamento; revisar "
            "max_connections considerando a quantidade de proxies/scripts conectando; "
            "innodb_io_capacity deve refletir o tipo de armazenamento (SSD/IOPS provisionado)."
        ),
        evidence=variables,
    )]


def _growth_findings(config, mysql: dict, size_history: list | None = None) -> list[Finding]:
    tables = mysql.get("tables", [])
    if not tables:
        return []
    total_gb = sum(t["data_length"] + t["index_length"] for t in tables) / (1024 ** 3)
    size_history = size_history or []

    if len(size_history) < 2:
        return [Finding(
            section="Banco de Dados",
            title="Análise de crescimento mensal",
            severity=Severity.MANUAL_REVIEW,
            description=(
                f"Ainda não há histórico suficiente de execuções para estimar crescimento "
                f"(tamanho atual: {total_gb:.2f} GB)."
            ),
            recommendation="Rodar o assessment periodicamente para este cliente para acumular histórico de tamanho.",
            evidence={"total_gb": round(total_gb, 2), "samples": len(size_history)},
        )]

    first, last = size_history[0], size_history[-1]
    days = max((last["timestamp"] - first["timestamp"]) / 86400, 1)
    monthly_gb = (last["total_gb"] - first["total_gb"]) / days * 30
    threshold = config.thresholds["db_growth_gb_per_month_warning"]
    severity = Severity.WARNING if monthly_gb >= threshold else Severity.INFO
    return [Finding(
        section="Banco de Dados",
        title="Análise de crescimento mensal",
        severity=severity,
        description=(
            f"Com base em {len(size_history)} execuções ao longo de {days:.0f} dias, "
            f"estima-se um crescimento de {monthly_gb:.1f} GB/mês."
        ),
        recommendation="Dimensionar armazenamento e revisar retenção/particionamento considerando essa taxa.",
        evidence={"monthly_gb": round(monthly_gb, 1), "samples": len(size_history)},
    )]


def _backup_policy_manual_review(config) -> list[Finding]:
    return [Finding(
        section="Banco de Dados",
        title="Política de backup e plano de restauração",
        severity=Severity.MANUAL_REVIEW,
        description=(
            "Este MVP não verifica automaticamente frequência de backup, retenção de "
            "snapshots nem a existência de um backup estrutural (schema-only)."
        ),
        recommendation=(
            "Confirmar manualmente: frequência/horário de backup, política de retenção, "
            "e se existe backup estrutural separado das tabelas de histórico "
            "(ex.: mysqldump ignorando history/history_uint/history_text/trends)."
        ),
        evidence={},
    )]


def _high_availability_cost_finding(config) -> list[Finding]:
    """Generic HA/cost manual-review note — phrased by hosting type instead of
    assuming AWS/RDS. `client.hosting_type` in the config is "cloud",
    "on-premise", "hybrid", or unset (generic wording, no assumption)."""
    hosting_type = (config.client.get("hosting_type") or "").lower()

    if hosting_type == "cloud":
        description = (
            "Este MVP não integra com a API de billing do provedor de nuvem. "
            "Verificação de alta disponibilidade (ex.: Multi-AZ/cluster gerenciado), "
            "política de snapshot e custo de instância fica pendente."
        )
        recommendation = (
            "Rodar coleta manual via CLI/Cost Explorer do provedor (AWS, Azure, GCP) "
            "ou aguardar coletor v2 com a API de billing específica."
        )
    elif hosting_type == "on-premise":
        description = (
            "Este MVP não avalia redundância de hardware (RAID, cluster local, "
            "nobreak/gerador) nem custo de licenciamento/manutenção on-premise."
        )
        recommendation = (
            "Levantar manualmente a estratégia de alta disponibilidade do hardware "
            "local e o custo de manutenção/licenciamento com o time de infraestrutura."
        )
    else:
        description = (
            "Este MVP não avalia automaticamente a estratégia de alta disponibilidade "
            "do banco (replicação/cluster, seja em nuvem ou on-premise) nem custo de "
            "infraestrutura associado."
        )
        recommendation = (
            "Levantar manualmente a estratégia de HA e custo — em nuvem (ex.: Multi-AZ, "
            "reserva de instância) ou on-premise (redundância de hardware, licenciamento). "
            "Preencher 'client.hosting_type' no config ajuda a direcionar essa checagem."
        )

    return [Finding(
        section="Banco de Dados",
        title="Alta disponibilidade e custo de infraestrutura do banco",
        severity=Severity.MANUAL_REVIEW,
        description=description,
        recommendation=recommendation,
        evidence={"hosting_type": hosting_type or "não informado"},
    )]


def evaluate(config, mysql: dict, zbx: dict | None = None, size_history: list | None = None) -> list[Finding]:
    findings = []
    findings += _mysql_eol_finding(config, mysql)
    findings += _zabbix_compat_finding(config, mysql)
    findings += _partitioning_findings(config, mysql)
    findings += _db_size_info(config, mysql)
    findings += _growth_findings(config, mysql, size_history)
    findings += _history_age_finding(config, mysql, zbx)
    findings += _tuning_findings(config, mysql)
    findings += _backup_policy_manual_review(config)
    findings += _high_availability_cost_finding(config)
    return findings
