"""Generazione del report: bandi.json, report.md e una pagina HTML consultabile (index.html)."""

from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path


def _sort_key(b: dict) -> tuple:
    s = b.get("scheda") or {}
    return (-(s.get("pertinenza") or 0), s.get("scadenza_domande") or "9999")


def _fmt(d: str | None) -> str:
    if not d:
        return "n.d."
    if len(d) < 10 or d[4] != "-":
        return d
    return f"{d[8:10]}/{d[5:7]}/{d[:4]}{d[10:]}"


def build_records(bandi: list[dict], oggi: str) -> list[dict]:
    records = []
    for b in sorted(bandi, key=_sort_key):
        s = b.get("scheda") or {}
        records.append({
            "url": b["url"],
            "fonte": b["fonte"],
            "nuovo": b["primo_rilevamento"] == oggi,
            "primo_rilevamento": b["primo_rilevamento"],
            "errore": b.get("errore"),
            **{k: v for k, v in s.items()},
            "titolo": s.get("titolo") or b["titolo"],
        })
    return records


def write_markdown(records: list[dict], path: Path, oggi: str) -> None:
    lines = [f"# Bandi attivi — aggiornamento del {_fmt(oggi)}", ""]
    nuovi = sum(r["nuovo"] for r in records)
    lines.append(f"{len(records)} bandi attivi, {nuovi} nuovi oggi. Ordinati per pertinenza.\n")
    for r in records:
        tag = " 🆕" if r["nuovo"] else ""
        lines += [
            f"## [{r['titolo']}]({r['url']}){tag}",
            "",
            f"- **Pertinenza:** {r.get('pertinenza', 'n.d.')}/100 — {r.get('motivo_pertinenza') or ''}",
            f"- **Ente / fonte:** {r.get('ente') or r['fonte']}",
            f"- **Pubblicazione:** {_fmt(r.get('data_pubblicazione'))} · "
            f"**Apertura domande:** {_fmt(r.get('apertura_domande'))} · "
            f"**Scadenza:** {_fmt(r.get('scadenza_domande'))}",
            f"- **Chi può partecipare:** {r.get('chi_puo_partecipare') or 'n.d.'}",
            f"- **Tipologia:** {r.get('tipologia') or 'n.d.'} · **Fondo perduto:** {r.get('fondo_perduto') or 'n.d.'}"
            + (f" ({r['percentuale_fondo_perduto']})" if r.get("percentuale_fondo_perduto") else ""),
        ]
        if r.get("importo_contributo"):
            lines.append(f"- **Contributo:** {r['importo_contributo']}")
        if r.get("dotazione_finanziaria"):
            lines.append(f"- **Dotazione:** {r['dotazione_finanziaria']}")
        for s in r.get("altre_scadenze") or []:
            lines.append(f"- **{s['descrizione']}:** {_fmt(s['data'])}")
        lines += ["", r.get("riassunto") or "_Riassunto non disponibile._", ""]
        if r.get("errore"):
            lines += [f"> ⚠️ {r['errore']}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


HTML_TEMPLATE = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monitor Bandi</title>
<style>
:root { --bg:#f7f7f5; --card:#fff; --fg:#1d1d1b; --muted:#666; --line:#e2e2de; --accent:#0b6e4f; --new:#c2410c; --chip:#eef3f0; }
@media (prefers-color-scheme: dark) { :root { --bg:#141414; --card:#1e1e1e; --fg:#ececec; --muted:#a3a3a3; --line:#333; --accent:#4fbf94; --new:#fb923c; --chip:#22302a; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width:1000px; margin:0 auto; padding:24px 16px 64px; }
h1 { font-size:1.5rem; margin:0 0 4px; }
.sub { color:var(--muted); margin:0 0 20px; }
.filters { display:flex; flex-wrap:wrap; gap:12px; align-items:center; margin-bottom:20px; }
.filters input[type=search] { flex:1 1 240px; padding:8px 10px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--fg); }
.filters label { color:var(--muted); font-size:.9rem; display:flex; gap:6px; align-items:center; }
.card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px 18px; margin-bottom:14px; }
.card h2 { font-size:1.05rem; margin:0 0 8px; }
.card h2 a { color:inherit; }
.meta { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:4px 16px; font-size:.9rem; margin:8px 0; }
.meta b { color:var(--muted); font-weight:500; }
.chips { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:6px; }
.chip { background:var(--chip); border-radius:999px; padding:2px 10px; font-size:.8rem; }
.chip.new { background:var(--new); color:#fff; }
.score { font-weight:700; color:var(--accent); }
.why { color:var(--muted); font-size:.88rem; }
.warn { color:var(--new); font-size:.88rem; }
details { font-size:.9rem; margin-top:6px; }
.empty { color:var(--muted); text-align:center; padding:40px; }
</style>
</head>
<body>
<main>
<h1>Monitor Bandi</h1>
<p class="sub">Aggiornato il __DATA__ · <span id="count"></span></p>
<div class="filters">
  <input type="search" id="q" placeholder="Cerca per titolo, beneficiari, tipologia…">
  <label>Pertinenza minima <input type="range" id="min" min="0" max="100" step="10" value="0"><span id="minv">0</span></label>
  <label><input type="checkbox" id="onlynew"> Solo nuovi</label>
  <label><input type="checkbox" id="fp"> Solo fondo perduto</label>
</div>
<div id="list"></div>
</main>
<script>
const DATA = __JSON__;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmt = d => d ? d.slice(8,10) + "/" + d.slice(5,7) + "/" + d.slice(0,4) + d.slice(10) : "n.d.";
function render() {
  const q = $("q").value.toLowerCase(), min = +$("min").value;
  $("minv").textContent = min;
  const rows = DATA.filter(r => (r.pertinenza ?? 0) >= min
    && (!$("onlynew").checked || r.nuovo)
    && (!$("fp").checked || r.fondo_perduto === "si" || r.fondo_perduto === "parziale")
    && (!q || JSON.stringify(r).toLowerCase().includes(q)));
  $("count").textContent = rows.length + " di " + DATA.length + " bandi attivi";
  $("list").innerHTML = rows.length ? rows.map(r => `
  <article class="card">
    <div class="chips">
      ${r.nuovo ? '<span class="chip new">Nuovo</span>' : ""}
      <span class="chip">${esc(r.tipologia || "tipologia n.d.")}</span>
      <span class="chip">Fondo perduto: ${esc(r.fondo_perduto || "n.d.")}${r.percentuale_fondo_perduto ? " · " + esc(r.percentuale_fondo_perduto) : ""}</span>
      <span class="chip">${esc(r.ente || r.fonte)}</span>
    </div>
    <h2><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.titolo)}</a></h2>
    <div class="meta">
      <div><b>Pubblicazione</b> ${fmt(r.data_pubblicazione)}</div>
      <div><b>Apertura domande</b> ${fmt(r.apertura_domande)}</div>
      <div><b>Scadenza</b> ${fmt(r.scadenza_domande)}</div>
      <div><b>Pertinenza</b> <span class="score">${r.pertinenza ?? "n.d."}/100</span></div>
    </div>
    <div class="meta"><div><b>Chi può partecipare</b> ${esc(r.chi_puo_partecipare || "n.d.")}</div></div>
    ${r.importo_contributo ? `<div class="meta"><div><b>Contributo</b> ${esc(r.importo_contributo)}</div></div>` : ""}
    <p>${esc(r.riassunto || "Riassunto non disponibile.")}</p>
    <p class="why">${esc(r.motivo_pertinenza || "")}</p>
    ${(r.altre_scadenze || []).length || (r.requisiti_principali || []).length || (r.spese_ammissibili || []).length ? `<details><summary>Dettagli</summary>
      ${(r.altre_scadenze || []).map(s => `<div>${esc(s.descrizione)}: ${fmt(s.data)}</div>`).join("")}
      ${(r.requisiti_principali || []).length ? "<p><b>Requisiti</b></p><ul>" + r.requisiti_principali.map(x => `<li>${esc(x)}</li>`).join("") + "</ul>" : ""}
      ${(r.spese_ammissibili || []).length ? "<p><b>Spese ammissibili</b></p><ul>" + r.spese_ammissibili.map(x => `<li>${esc(x)}</li>`).join("") + "</ul>" : ""}
      ${r.modalita_presentazione ? `<p><b>Presentazione:</b> ${esc(r.modalita_presentazione)}</p>` : ""}
      ${r.note ? `<p><b>Note:</b> ${esc(r.note)}</p>` : ""}
    </details>` : ""}
    ${r.errore ? `<p class="warn">⚠️ ${esc(r.errore)}</p>` : ""}
  </article>`).join("") : '<p class="empty">Nessun bando corrisponde ai filtri.</p>';
}
["q","min","onlynew","fp"].forEach(id => $(id).addEventListener("input", render));
render();
</script>
</body>
</html>
"""


def write_html(records: list[dict], path: Path, oggi: str) -> None:
    payload = json.dumps(records, ensure_ascii=False).replace("</", "<\\/")
    page = HTML_TEMPLATE.replace("__JSON__", payload).replace("__DATA__", html.escape(_fmt(oggi)))
    path.write_text(page, encoding="utf-8")


def write_reports(bandi: list[dict], cartella: Path, oggi: str | None = None) -> list[dict]:
    oggi = oggi or date.today().isoformat()
    cartella.mkdir(parents=True, exist_ok=True)
    records = build_records(bandi, oggi)
    (cartella / "bandi.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(records, cartella / "report.md", oggi)
    write_html(records, cartella / "index.html", oggi)
    return records
