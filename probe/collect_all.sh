#!/usr/bin/env bash
# Roda flowbix_probe.py via SSH em cada host informado e salva o JSON em ./coletas/.
#
# Uso:
#   ./probe/collect_all.sh usuario@zabbix-server usuario@VMPLNX5081 usuario@VMPLNX5099
#
# Cada arquivo de saída é nomeado pelo host (parte depois do @), então rodar
# de novo sobrescreve a coleta anterior daquele host.
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "Uso: $0 usuario@host1 [usuario@host2 ...]" >&2
  exit 1
fi

OUT_DIR="${OUT_DIR:-./coletas}"
mkdir -p "$OUT_DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROBE="$SCRIPT_DIR/flowbix_probe.py"

for target in "$@"; do
  host_label="${target#*@}"
  out_file="$OUT_DIR/${host_label}.json"
  echo "Coletando $target -> $out_file"
  ssh "$target" 'python3 -' < "$PROBE" > "$out_file"
done

echo "Concluído. $# arquivo(s) em $OUT_DIR/"
