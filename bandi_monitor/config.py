from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Fonte:
    nome: str
    url: str
    selettore_link: str | None = None
    regex_link: str | None = None
    max_pagine: int = 5


@dataclass
class Analisi:
    modello: str = "claude-opus-5-5"
    effort: str = "medium"
    max_caratteri: int = 400_000
    max_allegati: int = 15
    max_mb_allegato: int = 25


@dataclass
class Config:
    profilo: str
    parole_chiave: list[str]
    fonti: list[Fonte]
    analisi: Analisi = field(default_factory=Analisi)
    cartella_output: Path = Path("docs")
    database: Path = Path("data/bandi.db")


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    fonti = [Fonte(**f) for f in raw.get("fonti", [])]
    if not fonti:
        raise ValueError(f"Nessuna fonte configurata in {path}")
    out = raw.get("output", {}) or {}
    return Config(
        profilo=(raw.get("profilo") or "").strip(),
        parole_chiave=list(raw.get("parole_chiave") or []),
        fonti=fonti,
        analisi=Analisi(**(raw.get("analisi") or {})),
        cartella_output=Path(out.get("cartella", "docs")),
        database=Path(out.get("database", "data/bandi.db")),
    )
