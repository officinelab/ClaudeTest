from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS bandi (
    url TEXT PRIMARY KEY,
    fonte TEXT NOT NULL,
    titolo TEXT NOT NULL,
    primo_rilevamento TEXT NOT NULL,
    ultimo_rilevamento TEXT NOT NULL,
    ultima_analisi TEXT,
    hash TEXT,
    scheda TEXT,
    errore TEXT,
    attivo INTEGER NOT NULL DEFAULT 1
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def get(self, url: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM bandi WHERE url = ?", (url,)).fetchone()

    def seen(self, url: str, fonte: str, titolo: str, oggi: str) -> bool:
        """Registra il bando come presente oggi nell'elenco. Ritorna True se è nuovo."""
        row = self.get(url)
        if row is None:
            self.db.execute(
                "INSERT INTO bandi (url, fonte, titolo, primo_rilevamento, ultimo_rilevamento) VALUES (?,?,?,?,?)",
                (url, fonte, titolo, oggi, oggi))
            return True
        self.db.execute("UPDATE bandi SET titolo = ?, ultimo_rilevamento = ?, attivo = 1 WHERE url = ?",
                        (titolo, oggi, url))
        return False

    def save_analysis(self, url: str, oggi: str, hash_: str, scheda: dict | None, errore: str | None) -> None:
        self.db.execute(
            "UPDATE bandi SET ultima_analisi = ?, hash = ?, scheda = COALESCE(?, scheda), errore = ? WHERE url = ?",
            (oggi, hash_, json.dumps(scheda, ensure_ascii=False) if scheda else None, errore, url))

    def deactivate_missing(self, fonte: str, oggi: str) -> int:
        cur = self.db.execute("UPDATE bandi SET attivo = 0 WHERE fonte = ? AND ultimo_rilevamento < ? AND attivo = 1",
                              (fonte, oggi))
        return cur.rowcount

    def active(self) -> list[dict]:
        rows = self.db.execute("SELECT * FROM bandi WHERE attivo = 1").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["scheda"] = json.loads(d["scheda"]) if d["scheda"] else None
            out.append(d)
        return out

    def commit(self) -> None:
        self.db.commit()

    def close(self) -> None:
        self.db.commit()
        self.db.close()
