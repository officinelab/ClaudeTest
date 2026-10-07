"""Lettura delle pagine elenco e delle pagine di dettaglio dei bandi."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup, Tag

DOC_EXTENSIONS = (".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".xls", ".xlsx", ".ods", ".zip", ".p7m")
DOC_HINTS = ("download", "allegat", "documenti/", "/documents/", "getfile", "file=", "attachment")
NEXT_PAGE_TEXT = re.compile(r"^(\d{1,3}|succ\w*|next|avanti|>|»|›|>>)$", re.IGNORECASE)
MIN_TITLE_LEN = 20


@dataclass
class VoceElenco:
    url: str
    titolo: str
    contesto: str = ""  # testo attorno al link nell'elenco (spesso contiene le date)


@dataclass
class Dettaglio:
    url: str
    titolo: str
    testo: str
    allegati: list[tuple[str, str]] = field(default_factory=list)  # (testo link, url)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _abs(base: str, href: str | None) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None
    return urldefrag(urljoin(base, href))[0]


def _signature(url: str) -> tuple:
    """Forma dell'URL: host, percorso e nomi dei parametri (non i valori)."""
    p = urlparse(url)
    keys = tuple(sorted({k for k, _ in parse_qsl(p.query, keep_blank_values=True)}))
    path = p.path if keys else re.sub(r"[^/]+$", "", p.path)
    return (p.netloc.lower(), path, keys)


def is_document_url(url: str) -> bool:
    low = url.lower().split("?")[0]
    return low.endswith(DOC_EXTENSIONS) or any(h in url.lower() for h in DOC_HINTS)


def _strip_chrome(soup: BeautifulSoup) -> None:
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "form", "iframe", "svg"]):
        tag.decompose()
    for tag in soup.select('[role="navigation"], [class*="menu"], [id*="menu"], [class*="breadcrumb"], '
                           '[class*="cookie"], [id*="cookie"], [class*="footer"], [id*="footer"]'):
        tag.decompose()


def _context_for(a: Tag) -> str:
    """Testo del blocco che contiene il link (riga di tabella, elemento di lista o div)."""
    node: Tag | None = a
    for _ in range(4):
        node = node.parent if node is not None else None
        if node is None:
            break
        if node.name in ("li", "tr", "article", "dd") or (
            node.name in ("div", "p", "section") and len(_clean(node.get_text(" "))) > len(_clean(a.get_text(" "))) + 10
        ):
            text = _clean(node.get_text(" "))
            return text[:1000]
    return ""


def parse_list_page(html: str, page_url: str, selettore: str | None = None,
                    regex: str | None = None) -> list[VoceElenco]:
    soup = _soup(html)
    if selettore:
        anchors = [a for a in soup.select(selettore) if a.name == "a"] or [
            a for el in soup.select(selettore) for a in el.find_all("a")]
    else:
        _strip_chrome(soup)
        anchors = soup.find_all("a")

    candidati: list[VoceElenco] = []
    seen: set[str] = set()
    for a in anchors:
        url = _abs(page_url, a.get("href"))
        if not url or url == page_url or url in seen:
            continue
        titolo = _clean(a.get_text(" ")) or _clean(a.get("title", ""))
        if regex:
            if not re.search(regex, url):
                continue
        elif len(titolo) < MIN_TITLE_LEN or is_document_url(url):
            continue
        seen.add(url)
        candidati.append(VoceElenco(url=url, titolo=titolo, contesto=_context_for(a)))

    if selettore or regex or not candidati:
        return candidati

    # Riconoscimento automatico: i link ai bandi condividono la stessa "forma" di URL.
    # Si sceglie il gruppo con più testo (titoli lunghi), escludendo la forma della pagina stessa.
    gruppi: dict[tuple, list[VoceElenco]] = defaultdict(list)
    for v in candidati:
        gruppi[_signature(v.url)].append(v)
    own = _signature(page_url)

    def score(item: tuple[tuple, list[VoceElenco]]) -> tuple:
        sig, voci = item
        return (sig != own, sum(min(len(v.titolo), 200) for v in voci))

    best_sig, best = max(gruppi.items(), key=score)
    # Se il gruppo migliore ha un solo elemento, l'elenco probabilmente ha link eterogenei: tieni tutto.
    return best if len(best) > 1 else candidati


def find_next_pages(html: str, page_url: str) -> list[str]:
    """Link di paginazione: stessa forma URL della pagina elenco (± un parametro) e testo numerico/"successiva"."""
    soup = _soup(html)
    own_host, own_path, own_keys = _signature(page_url)
    pages: list[str] = []
    for a in soup.find_all("a"):
        text = _clean(a.get_text(" ")) or _clean(a.get("title", ""))
        url = _abs(page_url, a.get("href"))
        if not url or url == page_url or not NEXT_PAGE_TEXT.match(text):
            continue
        host, path, keys = _signature(url)
        if host == own_host and path == own_path and len(set(keys) ^ set(own_keys)) <= 1 and url not in pages:
            pages.append(url)
    return pages


def _attachments(root: Tag | BeautifulSoup, page_url: str) -> list[tuple[str, str]]:
    allegati: list[tuple[str, str]] = []
    seen: set[str] = set()
    for a in root.find_all("a"):
        url = _abs(page_url, a.get("href"))
        if url and url not in seen and is_document_url(url):
            seen.add(url)
            allegati.append((_clean(a.get_text(" ")) or url.rsplit("/", 1)[-1], url))
    return allegati


def parse_detail_page(html: str, page_url: str) -> Dettaglio:
    soup = _soup(html)
    tutti = _attachments(soup, page_url)
    h = soup.find("h1") or soup.find("h2") or soup.find("title")
    titolo = _clean(h.get_text(" ")) if h else ""
    _strip_chrome(soup)
    main = soup.find("main") or soup.find(id=re.compile("content|contenut|main", re.I)) or soup.body or soup
    testo = "\n".join(line for line in (_clean(t) for t in main.get_text("\n").splitlines()) if line)
    # Preferisci gli allegati del corpo pagina; se non ce ne sono, usa quelli dell'intera pagina.
    allegati = _attachments(main, page_url) or tutti
    return Dettaglio(url=page_url, titolo=titolo, testo=testo, allegati=allegati)
