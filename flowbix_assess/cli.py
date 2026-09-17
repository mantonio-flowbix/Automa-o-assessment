from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import Config
from .models import CollectionResult
from .collectors import zabbix as zabbix_collector
from .collectors import mysql_collector
from .collectors import grafana as grafana_collector
from .collectors import infra_local
from .rules import run_all
from .report import render
from .pptx_report import render as render_pptx

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"


def _collect_demo() -> CollectionResult:
    result = CollectionResult()
    result.zabbix = json.loads((SAMPLE_DIR / "zabbix_sample.json").read_text(encoding="utf-8"))
    result.mysql = json.loads((SAMPLE_DIR / "mysql_sample.json").read_text(encoding="utf-8"))
    result.infra = infra_local.collect(str(SAMPLE_DIR / "infra"))
    result.grafana = json.loads((SAMPLE_DIR / "grafana_sample.json").read_text(encoding="utf-8"))
    return result


def _collect_real(config: Config, infra_dir: str | None = None) -> CollectionResult:
    result = CollectionResult()

    infra_dir = infra_dir or config.infra_dir
    if infra_dir:
        try:
            result.infra = infra_local.collect(infra_dir)
        except Exception as exc:  # noqa: BLE001
            result.errors["infra"] = str(exc)
    else:
        result.errors["infra"] = "não configurado (use --infra-dir ou infra.collected_dir no config)"

    if config.zabbix:
        try:
            hosts_of_interest = config.zabbix.get("hosts_of_interest", [])
            result.zabbix = zabbix_collector.collect(
                config.zabbix,
                hosts_of_interest=hosts_of_interest,
                template_limit=config.zabbix.get("template_limit"),
            )
        except Exception as exc:  # noqa: BLE001 — surfaced in the report, not swallowed
            result.errors["zabbix"] = str(exc)
    else:
        result.errors["zabbix"] = "não configurado"

    if config.mysql:
        try:
            result.mysql = mysql_collector.collect(config.mysql)
        except Exception as exc:  # noqa: BLE001
            result.errors["mysql"] = str(exc)
    else:
        result.errors["mysql"] = "não configurado"

    if config.grafana:
        try:
            result.grafana = grafana_collector.collect(config.grafana)
        except Exception as exc:  # noqa: BLE001
            result.errors["grafana"] = str(exc)
    else:
        result.errors["grafana"] = "não configurado"

    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog="flowbix-assess")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Executa o assessment contra um ambiente real")
    run_parser.add_argument("--config", required=True, help="Caminho do YAML de configuração do cliente")
    run_parser.add_argument("--output", default="report.html", help="Arquivo HTML de saída")
    run_parser.add_argument("--pptx", default=None, help="Também gera a apresentação final nesse caminho .pptx")
    run_parser.add_argument(
        "--infra-dir", default=None,
        help="Pasta com os JSONs gerados pelo probe/flowbix_probe.py (sobrepõe infra.collected_dir do config)",
    )

    demo_parser = sub.add_parser("demo", help="Executa com dados de exemplo, sem credenciais reais")
    demo_parser.add_argument("--config", default=None, help="Config opcional (nome do cliente / thresholds)")
    demo_parser.add_argument("--output", default="report_demo.html", help="Arquivo HTML de saída")
    demo_parser.add_argument("--pptx", default=None, help="Também gera a apresentação final nesse caminho .pptx")

    args = parser.parse_args(argv)

    if args.command == "run":
        config = Config.load(args.config)
        collection = _collect_real(config, infra_dir=args.infra_dir)
    else:
        config = (
            Config.load(args.config) if args.config
            else Config({
                "client": {"name": "Cliente Demo", "target_zabbix_version": "7.0"},
                "infra": {
                    "hosts": [
                        {"name": "zabbix-server-varejo-prd", "role": "server"},
                        {"name": "VMPLNX5081", "role": "proxy"},
                        {"name": "VMPLNX5099", "role": "proxy"},
                    ]
                },
            })
        )
        collection = _collect_demo()

    findings = run_all(config, collection)
    path = render(config, findings, args.output, errors=collection.errors)

    print(f"Relatório gerado em: {path}")
    print(f"Total de achados: {len(findings)}")

    if args.pptx:
        pptx_path = render_pptx(config, findings, args.pptx)
        print(f"Apresentação gerada em: {pptx_path}")
    if collection.errors:
        print("Atenção — fontes não coletadas:")
        for source, message in collection.errors.items():
            print(f"  - {source}: {message}")


if __name__ == "__main__":
    main()
