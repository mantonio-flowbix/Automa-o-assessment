"""Per-client storage layout shared by the CLI and the web front-end.

Everything for a client lives under data/clients/<slug>/:
  config.yaml   — credentials + thresholds (plaintext on disk, local tool only)
  infra/        — where probe/flowbix_probe.py JSON outputs get dropped
  reports/      — generated HTML reports, timestamped
  _last_run/    — raw collector output from the most recent run, used to
                  hand data between the front-end's step-by-step endpoints
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CLIENTS_DIR = DATA_DIR / "clients"


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "cliente"


class ClientStore:
    def __init__(self, slug: str):
        self.slug = slug
        self.root = CLIENTS_DIR / slug
        self.infra_dir = self.root / "infra"
        self.reports_dir = self.root / "reports"
        self.run_dir = self.root / "_last_run"
        self.config_path = self.root / "config.yaml"
        self.env_path = self.root / ".env"

    def ensure(self):
        for d in (self.root, self.infra_dir, self.reports_dir, self.run_dir):
            d.mkdir(parents=True, exist_ok=True)

    def save_config(self, raw: dict):
        self.ensure()
        self.config_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def load_config_raw(self) -> dict:
        if not self.config_path.exists():
            return {}
        return yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}

    def load_env(self) -> dict:
        """Reads `<slug>/.env` (KEY=value per line) — where real secrets
        (tokens, passwords) live. `config.yaml` only ever holds `${KEY}`
        placeholders for these, so it stays safe to back up or version.
        """
        if not self.env_path.exists():
            return {}
        values = {}
        for line in self.env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
        return values

    def save_env(self, values: dict):
        """Merges `values` into the existing `.env` (blank/None values are
        dropped, not written) so leaving a credential field blank in the
        form keeps whatever was already saved instead of erasing it."""
        self.ensure()
        merged = {**self.load_env(), **{k: v for k, v in values.items() if v}}
        body = "\n".join(f"{k}={v}" for k, v in merged.items())
        self.env_path.write_text(body + ("\n" if body else ""), encoding="utf-8")

    def infra_files(self) -> list:
        self.ensure()
        return sorted(self.infra_dir.glob("*.json"))

    def save_run_artifact(self, name: str, data: dict):
        self.ensure()
        (self.run_dir / f"{name}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

    def load_run_artifact(self, name: str) -> dict:
        path = self.run_dir / f"{name}.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def append_size_history(self, total_gb: float, timestamp: float | None = None):
        """Appends one {timestamp, total_gb} sample so `database_rules` can
        estimate monthly growth across runs. Keeps at most the last 90
        samples — enough for a long trend without the file growing forever."""
        import time
        self.ensure()
        path = self.root / "db_size_history.json"
        history = self.load_size_history()
        history.append({"timestamp": timestamp or time.time(), "total_gb": total_gb})
        history = history[-90:]
        path.write_text(json.dumps(history), encoding="utf-8")
        return history

    def load_size_history(self) -> list:
        path = self.root / "db_size_history.json"
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def list_reports(self) -> list:
        self.ensure()
        files = list(self.reports_dir.glob("*.html")) + list(self.reports_dir.glob("*.pptx"))
        return sorted(files, key=lambda p: p.name, reverse=True)

    def clear_reports(self) -> int:
        """Deletes every generated report (HTML + PPTX) for this client.
        Returns how many files were removed."""
        files = self.list_reports()
        for path in files:
            path.unlink()
        return len(files)

    @classmethod
    def list_all(cls) -> list:
        if not CLIENTS_DIR.exists():
            return []
        return sorted(p.name for p in CLIENTS_DIR.iterdir() if p.is_dir())
