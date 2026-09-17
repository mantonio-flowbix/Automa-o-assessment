import json
from pathlib import Path

from flowbix_assess.collectors import infra_local
from flowbix_assess.config import Config
from flowbix_assess.models import CollectionResult
from flowbix_assess.rules import run_all

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
