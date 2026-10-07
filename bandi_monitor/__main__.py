"""Esecuzione: python -m bandi_monitor [--config config.yaml] [--rianalizza] [--limite N]"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import smtplib
import sys
from datetime import date
from email.message import EmailMessage
from pathlib import Path

from . import analyzer
from .config import Config, Fonte, load_config
from .documents import Documento, extract_text
from .http import Fetcher
from .report import write_reports
from .scraper import VoceElenco, find_next_pages, parse_detail_page, parse_list_page
from .storage import Store

log = logging.getLogger("bandi_monitor")


def scan_list(fetcher: Fetcher, fonte: Fonte) -> list[VoceElenco]:
    voci: dict[str, VoceElenco] = {}
    da_visitare, visitate = [fonte.url], set()
    while da_visitare and len(visitate) < fonte.max_pagine:
        url = da_visitare.pop(0)
        if url in visitate:
            continue
        visitate.add(url)
        html = fetcher.get(url).text
        nuove = parse_list_page(html, url, fonte.selettore_link, fonte.regex_link)
        log.info("  %s: %d bandi", url, len(nuove))
        for v in nuove:
            voci.setdefault(v.url, v)
        da_visitare += [p for p in find_next_pages(html, url) if p not in visitate]
    return list(voci.values())


def fetch_documents(fetcher: Fetcher, allegati: list[tuple[str, str]], cfg: Config) -> list[Documento]:
    docs = []
    for nome, url in allegati[: cfg.analisi.max_allegati]:
        try:
            resp = fetcher.get(url, max_bytes=cfg.analisi.max_mb_allegato * 1_000_000)
            docs.append(extract_text(resp.content, resp.url, resp.headers.get("Content-Type", ""), nome))
        except Exception as exc:
            docs.append(Documento(nome, url, "", errore=f"download fallito: {exc}"))
    if len(allegati) > cfg.analisi.max_allegati:
        log.warning("  %d allegati ignorati (max_allegati=%d)", len(allegati) - cfg.analisi.max_allegati,
                    cfg.analisi.max_allegati)
    return docs


def process_bando(fetcher: Fetcher, store: Store, cfg: Config, voce: VoceElenco, oggi: str,
                  forza: bool) -> None:
    resp = fetcher.get(voce.url)
    ctype = resp.headers.get("Content-Type", "")
    if "html" in ctype or not ctype:
        dettaglio = parse_detail_page(resp.text, resp.url)
        pagina, allegati = dettaglio.testo, dettaglio.allegati
    else:  # il link dell'elenco punta direttamente a un documento
        pagina, allegati = "", [(voce.titolo, voce.url)]

    impronta = hashlib.sha256(
        (pagina + "\n".join(u for _, u in allegati)).encode("utf-8", "replace")).hexdigest()
    row = store.get(voce.url)
    if not forza and row is not None and row["hash"] == impronta and row["scheda"] and not row["errore"]:
        log.info("  invariato: %s", voce.titolo[:80])
        return

    log.info("  analisi: %s (%d allegati)", voce.titolo[:80], len(allegati))
    documenti = fetch_documents(fetcher, allegati, cfg)
    kwargs = dict(url=voce.url, titolo_elenco=voce.titolo, contesto_elenco=voce.contesto,
                  pagina=pagina, documenti=documenti)
    errore = None
    if analyzer.has_api_key():
        try:
            scheda = analyzer.analyze_with_claude(
                **kwargs, profilo=cfg.profilo, modello=cfg.analisi.modello,
                effort=cfg.analisi.effort, max_chars=cfg.analisi.max_caratteri)
        except Exception as exc:
            log.error("  analisi AI fallita: %s", exc)
            errore = f"Analisi AI fallita ({type(exc).__name__}): scheda euristica"
            scheda = analyzer.analyze_heuristic(**kwargs, parole_chiave=cfg.parole_chiave)
    else:
        scheda = analyzer.analyze_heuristic(**kwargs, parole_chiave=cfg.parole_chiave)
    scheda["documenti_letti"] = [{"nome": d.nome, "url": d.url, "errore": d.errore} for d in documenti]
    store.save_analysis(voce.url, oggi, impronta, scheda, errore)


def send_email(records: list[dict], oggi: str) -> None:
    """Invia via SMTP i bandi nuovi, se configurate le variabili SMTP_* e EMAIL_TO."""
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("EMAIL_TO")
    nuovi = [r for r in records if r["nuovo"]]
    if not host or not to or not nuovi:
        return
    msg = EmailMessage()
    msg["Subject"] = f"{len(nuovi)} nuovi bandi — {oggi}"
    msg["From"] = os.environ.get("EMAIL_FROM") or os.environ.get("SMTP_USER") or to
    msg["To"] = to
    body = []
    for r in nuovi:
        body.append(
            f"{r['titolo']}\n{r['url']}\n"
            f"Pertinenza: {r.get('pertinenza', 'n.d.')}/100 · Scadenza: {r.get('scadenza_domande') or 'n.d.'}\n"
            f"Chi può partecipare: {r.get('chi_puo_partecipare') or 'n.d.'}\n"
            f"Tipologia: {r.get('tipologia') or 'n.d.'} · Fondo perduto: {r.get('fondo_perduto') or 'n.d.'} "
            f"{r.get('percentuale_fondo_perduto') or ''}\n\n{r.get('riassunto') or ''}\n")
    msg.set_content("\n" + ("\n" + "-" * 60 + "\n").join(body))
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)
    log.info("Email inviata a %s (%d bandi nuovi)", to, len(nuovi))


def run(cfg: Config, forza: bool = False, limite: int | None = None) -> int:
    oggi = date.today().isoformat()
    fetcher = Fetcher()
    store = Store(cfg.database)
    if not analyzer.has_api_key():
        log.warning("ANTHROPIC_API_KEY non impostata: uso l'estrazione euristica (senza riassunti AI).")
    errori_fonti = 0
    for fonte in cfg.fonti:
        log.info("Fonte: %s", fonte.nome)
        try:
            voci = scan_list(fetcher, fonte)
        except Exception as exc:
            log.error("Impossibile leggere l'elenco %s: %s", fonte.url, exc)
            errori_fonti += 1
            continue
        if not voci:
            log.error("Nessun bando trovato in %s: controlla selettore_link/regex_link", fonte.url)
            errori_fonti += 1
            continue
        for voce in voci[:limite]:
            nuovo = store.seen(voce.url, fonte.nome, voce.titolo, oggi)
            if nuovo:
                log.info("  NUOVO: %s", voce.titolo[:80])
            try:
                process_bando(fetcher, store, cfg, voce, oggi, forza)
            except Exception as exc:
                log.error("  errore su %s: %s", voce.url, exc)
                store.save_analysis(voce.url, oggi, "", None, f"Errore lettura bando: {exc}")
            store.commit()
        if limite is None:
            n = store.deactivate_missing(fonte.nome, oggi)
            if n:
                log.info("  %d bandi non più in elenco (chiusi/scaduti)", n)
    store.commit()
    records = write_reports(store.active(), cfg.cartella_output, oggi)
    store.close()
    log.info("Report scritto in %s (%d bandi attivi)", cfg.cartella_output, len(records))
    try:
        send_email(records, oggi)
    except Exception as exc:
        log.error("Invio email fallito: %s", exc)
    return 1 if errori_fonti == len(cfg.fonti) else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bandi_monitor", description="Monitor giornaliero dei bandi")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--rianalizza", action="store_true", help="rianalizza anche i bandi invariati")
    p.add_argument("--limite", type=int, help="analizza al massimo N bandi per fonte (per prove)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    return run(load_config(Path(args.config)), forza=args.rianalizza, limite=args.limite)


if __name__ == "__main__":
    raise SystemExit(main())
