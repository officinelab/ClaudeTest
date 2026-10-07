"""Analisi del bando: Claude legge pagina e allegati e compila una scheda strutturata.

Senza ANTHROPIC_API_KEY si usa un'estrazione euristica (regex) molto più limitata.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re

from .documents import Documento

log = logging.getLogger(__name__)

TIPOLOGIE = [
    "contributo a fondo perduto",
    "finanziamento agevolato",
    "misto (fondo perduto + finanziamento)",
    "voucher",
    "credito d'imposta / agevolazione fiscale",
    "garanzia",
    "premio / concorso",
    "servizi / accompagnamento",
    "borsa / assegno di ricerca",
    "avviso / manifestazione di interesse",
    "altro",
]

_NSTR = {"type": ["string", "null"]}

SCHEMA = {
    "type": "object",
    "properties": {
        "titolo": {"type": "string"},
        "ente": _NSTR,
        "data_pubblicazione": {**_NSTR, "description": "YYYY-MM-DD"},
        "apertura_domande": {**_NSTR, "description": "Da quando si può presentare la domanda, YYYY-MM-DD (o YYYY-MM-DD HH:MM)"},
        "scadenza_domande": {**_NSTR, "description": "Termine ultimo per presentare la domanda, YYYY-MM-DD (o YYYY-MM-DD HH:MM)"},
        "altre_scadenze": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"descrizione": {"type": "string"}, "data": {"type": "string"}},
                "required": ["descrizione", "data"],
                "additionalProperties": False,
            },
        },
        "chi_puo_partecipare": {"type": "string"},
        "requisiti_principali": {"type": "array", "items": {"type": "string"}},
        "tipologia": {"type": "string", "enum": TIPOLOGIE},
        "fondo_perduto": {"type": "string", "enum": ["si", "no", "parziale", "non specificato"]},
        "percentuale_fondo_perduto": {**_NSTR, "description": "Es. '50%', 'fino all'80%', '40-60% secondo la dimensione d'impresa'"},
        "importo_contributo": {**_NSTR, "description": "Importo massimo/minimo per beneficiario"},
        "dotazione_finanziaria": {**_NSTR, "description": "Dotazione complessiva del bando"},
        "spese_ammissibili": {"type": "array", "items": {"type": "string"}},
        "modalita_presentazione": _NSTR,
        "riassunto": {"type": "string", "description": "5-8 frasi in italiano"},
        "pertinenza": {"type": "integer", "description": "0-100: quanto il bando è adatto al profilo dell'utente"},
        "motivo_pertinenza": {"type": "string"},
        "note": _NSTR,
    },
    "required": [
        "titolo", "ente", "data_pubblicazione", "apertura_domande", "scadenza_domande", "altre_scadenze",
        "chi_puo_partecipare", "requisiti_principali", "tipologia", "fondo_perduto", "percentuale_fondo_perduto",
        "importo_contributo", "dotazione_finanziaria", "spese_ammissibili", "modalita_presentazione",
        "riassunto", "pertinenza", "motivo_pertinenza", "note",
    ],
    "additionalProperties": False,
}

SYSTEM = """Sei un consulente esperto di finanza agevolata italiana ed europea.
Ricevi la pagina web di un bando e il testo dei suoi documenti (avviso, disciplinare, allegati, FAQ).
Compila la scheda in italiano basandoti SOLO sui contenuti forniti: se un dato non è presente scrivi null
(o "non specificato"), non inventarlo. Le date vanno in formato YYYY-MM-DD (aggiungi HH:MM se indicata l'ora).
Distingui la data di pubblicazione, l'apertura dello sportello per le domande e la scadenza di presentazione.
Per il fondo perduto indica la percentuale di intensità d'aiuto sulle spese ammissibili e le eventuali
differenze per dimensione d'impresa o zona. In "chi_puo_partecipare" indica beneficiari ammessi
(es. PMI, liberi professionisti, enti pubblici, ricercatori, persone fisiche) e i vincoli territoriali.
Valuta la pertinenza rispetto al profilo dell'utente: 0 = non può partecipare / non interessante, 100 = perfetto."""


def _documents_block(pagina: str, documenti: list[Documento], max_chars: int) -> tuple[str, bool]:
    parts = [f"=== PAGINA DEL BANDO ===\n{pagina}"]
    for d in documenti:
        if d.testo:
            parts.append(f"=== DOCUMENTO: {d.nome} ({d.url}) ===\n{d.testo}")
        elif d.errore:
            parts.append(f"=== DOCUMENTO: {d.nome} ({d.url}) === [non leggibile: {d.errore}]")
    full = "\n\n".join(parts)
    if len(full) <= max_chars:
        return full, False
    return full[:max_chars] + "\n\n[... TESTO TRONCATO PER LIMITE DI LUNGHEZZA ...]", True


def analyze_with_claude(*, url: str, titolo_elenco: str, contesto_elenco: str, pagina: str,
                        documenti: list[Documento], profilo: str, modello: str, effort: str,
                        max_chars: int) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    testo, troncato = _documents_block(pagina, documenti, max_chars)
    if troncato:
        log.warning("Testo troncato a %d caratteri per %s", max_chars, url)

    content: list[dict] = []
    # PDF scansionati (senza testo): li passiamo a Claude come documenti, che li legge direttamente.
    budget = 20_000_000
    for d in documenti:
        if d.pdf_bytes and len(d.pdf_bytes) * 4 // 3 < budget:
            budget -= len(d.pdf_bytes) * 4 // 3
            content.append({
                "type": "document",
                "title": d.nome[:200],
                "source": {"type": "base64", "media_type": "application/pdf",
                           "data": base64.standard_b64encode(d.pdf_bytes).decode()},
            })
    content.append({"type": "text", "text": (
        f"PROFILO DELL'UTENTE:\n{profilo}\n\n"
        f"URL DEL BANDO: {url}\n"
        f"TITOLO NELL'ELENCO: {titolo_elenco}\n"
        f"TESTO NELL'ELENCO: {contesto_elenco}\n\n"
        f"{testo}"
    )})

    with client.beta.messages.stream(
        model=modello,
        max_tokens=32000,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise RuntimeError(f"Richiesta rifiutata dal modello per {url}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError(f"Risposta troncata (max_tokens) per {url}")
    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)
    data["testo_troncato"] = troncato
    data["analisi"] = f"claude ({response.model})"
    return data


# ---------------------------------------------------------------- fallback senza API

MESI = {m: i for i, m in enumerate(
    ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto",
     "settembre", "ottobre", "novembre", "dicembre"], 1)}
_DATE = re.compile(
    r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})|(\d{1,2})\s+(" + "|".join(MESI) + r")\s+(\d{4})", re.IGNORECASE)


def _dates_after(keyword_re: str, text: str) -> str | None:
    for m in re.finditer(keyword_re, text, re.IGNORECASE):
        d = _DATE.search(text, m.end(), m.end() + 160)
        if d:
            if d.group(1):
                day, month, year = int(d.group(1)), int(d.group(2)), int(d.group(3))
            else:
                day, month, year = int(d.group(4)), MESI[d.group(5).lower()], int(d.group(6))
            if 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def analyze_heuristic(*, url: str, titolo_elenco: str, contesto_elenco: str, pagina: str,
                      documenti: list[Documento], parole_chiave: list[str]) -> dict:
    testo = "\n".join([contesto_elenco, pagina] + [d.testo for d in documenti])
    low = testo.lower()
    perc = re.search(r"(fondo perduto[^.%]{0,120}?(\d{1,3}(?:[.,]\d+)?\s?%))|((\d{1,3}(?:[.,]\d+)?\s?%)[^.%]{0,80}fondo perduto)",
                     testo, re.IGNORECASE)
    fp = "si" if "fondo perduto" in low else "non specificato"
    hits = [k for k in parole_chiave if k.lower() in low]
    return {
        "titolo": titolo_elenco,
        "ente": None,
        "data_pubblicazione": _dates_after(r"pubblicat\w*|data di pubblicazione", testo),
        "apertura_domande": _dates_after(r"a partire da|dalle ore|apertura|a decorrere da|dal giorno", testo),
        "scadenza_domande": _dates_after(r"scadenza|entro (?:e non oltre )?(?:il|le ore)|termine", testo),
        "altre_scadenze": [],
        "chi_puo_partecipare": "non determinato (analisi senza AI)",
        "requisiti_principali": [],
        "tipologia": "contributo a fondo perduto" if fp == "si" else "altro",
        "fondo_perduto": fp,
        "percentuale_fondo_perduto": (perc.group(2) or perc.group(4)) if perc else None,
        "importo_contributo": None,
        "dotazione_finanziaria": None,
        "spese_ammissibili": [],
        "modalita_presentazione": None,
        "riassunto": (pagina[:600] + "…") if len(pagina) > 600 else pagina,
        "pertinenza": min(100, 20 * len(hits)),
        "motivo_pertinenza": f"Parole chiave trovate: {', '.join(hits)}" if hits else "Nessuna parola chiave trovata",
        "note": "Scheda generata senza AI: imposta ANTHROPIC_API_KEY per riassunti completi.",
        "testo_troncato": False,
        "analisi": "euristica",
    }


def has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
