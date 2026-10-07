import io
import json
from pathlib import Path

import docx

from bandi_monitor import analyzer, report
from bandi_monitor.documents import extract_text
from bandi_monitor.scraper import find_next_pages, parse_detail_page, parse_list_page
from bandi_monitor.storage import Store

FIX = Path(__file__).parent / "fixtures"
LIST_URL = "https://www.sardegnaricerche.it/index.php?xsl=558&v=9&s=13&c=4200&nc=1&tipodoc=3&esito=0&scaduti=0"


def test_list_page_finds_only_bandi():
    voci = parse_list_page((FIX / "elenco.html").read_text(), LIST_URL)
    assert [v.url.split("s=")[1][:6] for v in voci] == ["500001", "500002", "500003"]
    assert voci[0].titolo.startswith("Voucher digitalizzazione")
    assert "Scadenza 30/11/2026" in voci[0].contesto


def test_list_page_custom_regex():
    voci = parse_list_page((FIX / "elenco.html").read_text(), LIST_URL, regex=r"s=50000[12]")
    assert len(voci) == 2


def test_pagination():
    pages = find_next_pages((FIX / "elenco.html").read_text(), LIST_URL)
    assert pages == [LIST_URL + "&pg=2"]


def test_detail_page_attachments():
    url = "https://www.sardegnaricerche.it/index.php?xsl=370&s=500001&v=2&c=4200&t=1"
    d = parse_detail_page((FIX / "dettaglio.html").read_text(), url)
    assert d.titolo.startswith("Voucher digitalizzazione")
    assert [n for n, _ in d.allegati] == ["Avviso pubblico", "Modulo di domanda"]
    assert d.allegati[0][1] == "https://www.sardegnaricerche.it/documenti/13_1_20260901_avviso.pdf"
    assert "70%" in d.testo


def test_extract_docx():
    buf = io.BytesIO()
    doc = docx.Document()
    doc.add_paragraph("Beneficiari: liberi professionisti iscritti all'albo")
    doc.save(buf)
    out = extract_text(buf.getvalue(), "https://x/modulo.docx")
    assert "liberi professionisti" in out.testo and out.errore is None


def test_extract_unknown_format_is_reported():
    out = extract_text(b"\x00\x01binary", "https://x/file.bin")
    assert out.testo == "" and out.errore


def test_heuristic_analysis():
    d = parse_detail_page((FIX / "dettaglio.html").read_text(), "https://x/b")
    s = analyzer.analyze_heuristic(url="https://x/b", titolo_elenco=d.titolo, contesto_elenco="",
                                   pagina=d.testo, documenti=[], parole_chiave=["professionist", "energ"])
    assert s["data_pubblicazione"] == "2026-09-01"
    assert s["apertura_domande"] == "2026-09-15"
    assert s["scadenza_domande"] == "2026-11-30"
    assert s["fondo_perduto"] == "si" and s["percentuale_fondo_perduto"] == "70%"
    assert s["pertinenza"] == 20


def test_schema_requires_every_property():
    assert set(analyzer.SCHEMA["required"]) == set(analyzer.SCHEMA["properties"])


def test_store_and_reports(tmp_path):
    store = Store(tmp_path / "b.db")
    assert store.seen("https://x/1", "F", "Bando uno", "2026-10-06") is True
    assert store.seen("https://x/2", "F", "Bando due", "2026-10-06") is True
    store.save_analysis("https://x/1", "2026-10-06", "h", {"titolo": "Bando uno", "pertinenza": 80}, None)
    # il giorno dopo il bando 2 non è più in elenco
    assert store.seen("https://x/1", "F", "Bando uno", "2026-10-07") is False
    assert store.seen("https://x/3", "F", "Bando tre", "2026-10-07") is True
    assert store.deactivate_missing("F", "2026-10-07") == 1
    records = report.write_reports(store.active(), tmp_path / "out", "2026-10-07")
    assert [r["titolo"] for r in records] == ["Bando uno", "Bando tre"]
    assert [r["nuovo"] for r in records] == [False, True]
    assert json.loads((tmp_path / "out" / "bandi.json").read_text())[0]["pertinenza"] == 80
    assert "Bando tre" in (tmp_path / "out" / "index.html").read_text()
    assert "🆕" in (tmp_path / "out" / "report.md").read_text()
