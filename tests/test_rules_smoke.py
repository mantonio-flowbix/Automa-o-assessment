import json
from pathlib import Path

from flowbix_assess.collectors import infra_local
from flowbix_assess.config import Config
from flowbix_assess.models import CollectionResult, Severity
from flowbix_assess.rules import run_all
from flowbix_assess.rules import zabbix_rules

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"


def _load_demo_collection() -> CollectionResult:
    result = CollectionResult()
    result.zabbix = json.loads((SAMPLE_DIR / "zabbix_sample.json").read_text())
    result.mysql = json.loads((SAMPLE_DIR / "mysql_sample.json").read_text())
    result.infra = infra_local.collect(str(SAMPLE_DIR / "infra"))
    result.grafana = json.loads((SAMPLE_DIR / "grafana_sample.json").read_text())
    return result


def _demo_config() -> Config:
    return Config({
        "client": {"name": "Cliente Demo", "target_zabbix_version": "7.0"},
        "infra": {
            "hosts": [
                {"name": "zabbix-server-varejo-prd", "role": "server"},
                {"name": "VMPLNX5081", "role": "proxy"},
                {"name": "VMPLNX5099", "role": "proxy"},
            ]
        },
    })


def test_engine_produces_findings_for_every_source():
    config = _demo_config()
    collection = _load_demo_collection()
    findings = run_all(config, collection)

    sections = {f.section for f in findings}
    assert "Arquitetura do Ambiente" in sections
    assert "Processamento das Máquinas" in sections
    assert "Análise de Templates" in sections
    assert "Banco de Dados" in sections
    assert "Infraestrutura" in sections
    assert "Grafana" in sections
    assert len(findings) > 5


def test_zabbix_architecture_and_tuning_rules_detected():
    config = _demo_config()
    collection = _load_demo_collection()
    findings = run_all(config, collection)

    titles = [f.title for f in findings]
    assert any("Topologia do ambiente" in t for t in titles)
    assert any("sem criptografia PSK" in t for t in titles)
    assert any("Forma de acesso ao ambiente" in t for t in titles)
    assert any("Processos internos do Zabbix sobrecarregados" in t for t in titles)
    assert any("conexão direta ao banco via ODBC" in t for t in titles)
    assert any("Triggers somando valores" in t for t in titles)
    assert any("Causas dos itens 'unsupported'" in t for t in titles)
    assert any("intervalo de coleta curto" in t for t in titles)


def test_database_tuning_and_compat_rules_detected():
    config = _demo_config()
    collection = _load_demo_collection()
    findings = run_all(config, collection)

    titles = [f.title for f in findings]
    assert any("Parâmetros de tuning do banco" in t for t in titles)
    assert any("Política de backup" in t for t in titles)
    assert any("Análise de crescimento mensal" in t for t in titles)
    # 8.0.35 satisfies the demo's Zabbix-7.0 minimum (8.0) — must NOT fire.
    assert not any("abaixo do mínimo exigido pelo Zabbix" in t for t in titles)


def test_infra_rules_detected():
    config = _demo_config()
    collection = _load_demo_collection()
    findings = run_all(config, collection)

    titles = [f.title for f in findings]
    assert any("SO incompatível com Zabbix" in t for t in titles)
    assert any("Carga de CPU elevada" in t for t in titles)
    assert any("Versão de zabbix_proxy divergente" in t for t in titles)
    assert any("Portas esperadas não detectadas" in t for t in titles)


def test_master_item_history_duplication_detected():
    config = _demo_config()
    collection = _load_demo_collection()
    findings = run_all(config, collection)

    titles = [f.title for f in findings]
    assert any("histórico duplicado" in t for t in titles)


def test_mysql_eol_and_partitioning_detected():
    config = _demo_config()
    collection = _load_demo_collection()
    findings = run_all(config, collection)

    titles = [f.title for f in findings]
    assert any("fim de suporte" in t for t in titles)
    assert any("sem particionamento" in t for t in titles)


def test_grafana_sqlite_backend_flagged():
    config = _demo_config()
    collection = _load_demo_collection()
    findings = run_all(config, collection)

    titles = [f.title for f in findings]
    assert any("SQLite" in t for t in titles)


def test_per_template_findings_are_aggregated_into_one():
    """Real environments can have hundreds of templates tripping the same
    rule — this guards against regressing to one finding per template
    (which is what turned a single assessment into ~750 PPTX slides)."""
    config = _demo_config()
    zbx = {
        "templates": [
            {
                "name": "Template A", "items": [
                    {"itemid": "1", "name": "item-a1", "delay": "10s", "triggers": []},
                    {"itemid": "2", "name": "item-a2", "delay": "10s", "triggers": []},
                ],
                "discovery_rules": [], "triggers": [],
            },
            {
                "name": "Template B", "items": [
                    {"itemid": "3", "name": "item-b1", "delay": "5s", "triggers": []},
                ],
                "discovery_rules": [], "triggers": [],
            },
            {
                "name": "Template C", "items": [
                    {"itemid": "4", "name": "item-c1", "delay": "60s", "triggers": []},
                ],
                "discovery_rules": [], "triggers": [],
            },
        ]
    }

    findings = zabbix_rules.evaluate(config, zbx)
    short_interval = [f for f in findings if "intervalo de coleta curto" in f.title]

    assert len(short_interval) == 1, "expected exactly one aggregated finding, not one per template"
    finding = short_interval[0]
    assert "2 template(s)" in finding.title
    assert "Template A" in finding.description and "Template B" in finding.description
    assert "Template C" not in finding.description
    assert len(finding.evidence["templates"]) == 2


def test_deadline_is_set_per_topic_not_a_flat_severity_bucket():
    """deadline_days is judged per finding topic, not a uniform per-severity
    mapping — a saturated disk (critical) and a stale dashboard name
    (manual review) shouldn't get the same urgency treatment. This pins a
    few representative cases rather than asserting a single formula."""
    config = _demo_config()
    collection = _load_demo_collection()
    findings = run_all(config, collection)
    by_title = {f.title: f for f in findings}

    # Purely informational finding: no deadline at all.
    topology = next(f for f in findings if "Topologia do ambiente" in f.title)
    assert topology.deadline_days is None

    # Disk/CPU-class infra findings get a short, operational deadline.
    cpu_host = next(f for f in findings if "Carga de CPU elevada" in f.title)
    assert cpu_host.deadline_days in (7, 30)

    # Every finding with a deadline has a plausible positive day count.
    for f in findings:
        if f.deadline_days is not None:
            assert 0 < f.deadline_days <= 120


def test_odbc_severity_escalates_when_any_template_has_errors():
    config = _demo_config()
    zbx = {
        "templates": [
            {
                "name": "Template ODBC OK",
                "items": [{"itemid": "1", "name": "q1", "key_": "db.odbc.select[x]", "type": "11", "error": ""}],
                "discovery_rules": [], "triggers": [],
            },
            {
                "name": "Template ODBC com erro",
                "items": [{"itemid": "2", "name": "q2", "key_": "db.odbc.select[y]", "type": "11", "error": "too many connections"}],
                "discovery_rules": [], "triggers": [],
            },
        ]
    }

    findings = zabbix_rules.evaluate(config, zbx)
    odbc = [f for f in findings if "conexão direta ao banco via ODBC" in f.title]

    assert len(odbc) == 1
    assert odbc[0].severity == Severity.WARNING
    assert "2 template(s)" in odbc[0].title
