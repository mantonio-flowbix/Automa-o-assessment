from __future__ import annotations

from ..models import Finding, Severity

# Starting point only — override/extend via thresholds.os_compatibility_matrix
# in the client config. Confirm against the official Zabbix docs for the
# target version before relying on this for a go/no-go decision.
DEFAULT_OS_COMPAT = {
    "7.0": {"ubuntu": ["22.04", "24.04"], "debian": ["11", "12"], "rhel": ["8", "9"], "centos": ["8", "9"]},
    "6.4": {"ubuntu": ["20.04", "22.04"], "debian": ["10", "11"], "rhel": ["7", "8", "9"]},
    "6.0": {"ubuntu": ["18.04", "20.04", "22.04"], "debian": ["9", "10", "11"]},
}

# Ordered release sequence per OS, used only to suggest an incremental
# upgrade path (you generally can't jump straight to a distant version).
# Starting point only — override via thresholds.os_upgrade_sequence.
DEFAULT_OS_UPGRADE_SEQUENCE = {
    "ubuntu": ["16.04", "18.04", "20.04", "22.04", "24.04"],
    "debian": ["9", "10", "11", "12"],
    "rhel": ["7", "8", "9"],
    "centos": ["7", "8", "9"],
}


def _suggest_upgrade_path(sequence: list, current_version: str, supported_versions: list):
    if current_version not in sequence:
        return None
    start = sequence.index(current_version)
    for version in sequence[start + 1:]:
        if version in supported_versions:
            return sequence[start + 1: sequence.index(version) + 1]
    return None


def _os_compat_findings(config, infra: dict) -> list[Finding]:
    matrix = {**DEFAULT_OS_COMPAT, **config.thresholds.get("os_compatibility_matrix", {})}
    target = config.target_zabbix_version
    compat = matrix.get(target)
    if not compat:
        return []

    findings = []
    for host, data in infra.get("hosts", {}).items():
        os_info = data.get("os", {})
        os_name = (os_info.get("name") or "").lower()
        os_version = os_info.get("version")
        if not os_name or not os_version:
            continue
        supported_versions = compat.get(os_name)
        if supported_versions is None:
            findings.append(Finding(
                section="Infraestrutura",
                title=f"SO não mapeado na matriz de compatibilidade em '{host}'",
                severity=Severity.MANUAL_REVIEW,
                description=(
                    f"{os_info.get('pretty_name') or os_name} não consta na matriz configurada "
                    f"para Zabbix {target}."
                ),
                recommendation="Verificar manualmente a matriz oficial de SO suportado pelo Zabbix.",
                evidence={"host": host, "os": os_info},
            ))
        elif os_version not in supported_versions:
            upgrade_sequence_map = {
                **DEFAULT_OS_UPGRADE_SEQUENCE,
                **config.thresholds.get("os_upgrade_sequence", {}),
            }
            path = _suggest_upgrade_path(
                upgrade_sequence_map.get(os_name, []), os_version, supported_versions
            )
            recommendation = f"Planejar upgrade incremental do SO antes de atualizar para Zabbix {target}."
            if path:
                recommendation += f" Caminho sugerido: {' → '.join([os_version] + path)}."
            findings.append(Finding(
                section="Infraestrutura",
                title=f"SO incompatível com Zabbix {target} em '{host}'",
                severity=Severity.CRITICAL,
                description=(
                    f"{os_info.get('pretty_name') or f'{os_name} {os_version}'} não é suportado; "
                    f"versões suportadas: {', '.join(supported_versions)}."
                ),
                recommendation=recommendation,
                evidence={"host": host, "os": os_info, "supported": supported_versions, "upgrade_path": path},
            ))
    return findings


def _resource_findings(config, infra: dict) -> list[Finding]:
    findings = []
    cpu_warn = config.thresholds["cpu_warning_pct"]
    cpu_crit = config.thresholds["cpu_critical_pct"]
    mem_warn = config.thresholds.get("memory_warning_pct", 80)
    mem_crit = config.thresholds.get("memory_critical_pct", 90)
    disk_warn = config.thresholds.get("disk_warning_pct", 80)
    disk_crit = config.thresholds.get("disk_critical_pct", 90)

    for host, data in infra.get("hosts", {}).items():
        cpu = data.get("cpu") or {}
        load_pct = cpu.get("load_pct_1min")
        if load_pct is not None:
            severity = None
            if load_pct >= cpu_crit:
                severity = Severity.CRITICAL
            elif load_pct >= cpu_warn:
                severity = Severity.WARNING
            if severity:
                findings.append(Finding(
                    section="Infraestrutura",
                    title=f"Carga de CPU elevada em '{host}'",
                    severity=severity,
                    description=(
                        f"Load average de 1 min equivalente a {load_pct}% da capacidade "
                        f"({cpu.get('count')} vCPUs)."
                    ),
                    recommendation="Investigar processos consumindo CPU; considerar upgrade se sustentado.",
                    evidence={"host": host, **cpu},
                ))

        memory = data.get("memory") or {}
        used_pct = memory.get("used_pct")
        if used_pct is not None:
            severity = None
            if used_pct >= mem_crit:
                severity = Severity.CRITICAL
            elif used_pct >= mem_warn:
                severity = Severity.WARNING
            if severity:
                findings.append(Finding(
                    section="Infraestrutura",
                    title=f"Uso de memória elevado em '{host}'",
                    severity=severity,
                    description=f"{used_pct}% da memória em uso ({memory.get('total_mb')} MB total).",
                    recommendation="Avaliar aumento de memória ou revisar processos consumidores.",
                    evidence={"host": host, **memory},
                ))

        for disk in data.get("disk", []):
            used_pct = disk.get("used_pct")
            if used_pct is None:
                continue
            severity = None
            if used_pct >= disk_crit:
                severity = Severity.CRITICAL
            elif used_pct >= disk_warn:
                severity = Severity.WARNING
            if not severity:
                continue
            findings.append(Finding(
                section="Infraestrutura",
                title=f"Disco '{disk['path']}' com pouco espaço livre em '{host}'",
                severity=severity,
                description=f"{used_pct}% ocupado ({disk['used_gb']}GB de {disk['total_gb']}GB).",
                recommendation="Liberar espaço ou expandir volume antes que afete a operação.",
                evidence={"host": host, **disk},
            ))
    return findings


def _zabbix_version_drift_findings(config, infra: dict, zbx: dict | None) -> list[Finding]:
    api_version = (zbx or {}).get("version")
    if not api_version:
        return []

    api_minor = ".".join(api_version.split(".")[:2])
    findings = []
    for host, data in infra.get("hosts", {}).items():
        for binary, version in (data.get("zabbix_binary_versions") or {}).items():
            if not version:
                continue
            installed_minor = ".".join(version.split(".")[:2])
            if installed_minor != api_minor:
                findings.append(Finding(
                    section="Infraestrutura",
                    title=f"Versão de {binary} divergente em '{host}'",
                    severity=Severity.WARNING,
                    description=(
                        f"Binário instalado ({version}) difere da versão reportada pela API "
                        f"do Zabbix ({api_version})."
                    ),
                    recommendation="Confirmar se todos os componentes (server/proxies) estão na mesma versão major.minor.",
                    evidence={"host": host, "binary": binary, "installed": version, "api_version": api_version},
                ))
    return findings


def _missing_ports_findings(config, infra: dict) -> list[Finding]:
    role_ports = config.thresholds.get("expected_ports_by_role", {})
    host_roles = {h["name"]: h.get("role") for h in config.infra_hosts}
    if not role_ports or not host_roles:
        return []

    findings = []
    for host, data in infra.get("hosts", {}).items():
        role = host_roles.get(host)
        expected = role_ports.get(role) if role else None
        if not expected:
            continue
        open_ports = set(data.get("listening_ports") or [])
        missing = [p for p in expected if p not in open_ports]
        if missing:
            findings.append(Finding(
                section="Infraestrutura",
                title=f"Portas esperadas não detectadas em '{host}'",
                severity=Severity.MANUAL_REVIEW,
                description=(
                    f"Portas {missing} não detectadas ouvindo (papel: {role}). Pode ser bloqueio "
                    f"de firewall local, serviço parado, ou probe rodando sem permissão para 'ss'."
                ),
                recommendation="Confirmar manualmente se o serviço está ativo e a porta liberada.",
                evidence={"host": host, "missing_ports": missing, "role": role},
            ))
    return findings


def evaluate(config, infra: dict, zbx: dict | None = None) -> list[Finding]:
    findings = []
    findings += _os_compat_findings(config, infra)
    findings += _resource_findings(config, infra)
    findings += _zabbix_version_drift_findings(config, infra, zbx)
    findings += _missing_ports_findings(config, infra)
    return findings
