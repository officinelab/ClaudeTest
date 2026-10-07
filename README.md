# Monitor Bandi

App che ogni giorno legge gli elenchi di bandi che le indichi, apre ogni bando, scarica e legge
i documenti allegati (PDF, Word, ODT, ZIP…) e per ciascuno compila una scheda con:

- **titolo** ed ente
- **data di pubblicazione**, **apertura** e **scadenza** per la presentazione della domanda (più eventuali altre scadenze)
- **chi può partecipare** e requisiti principali
- **tipologia** (fondo perduto, finanziamento agevolato, voucher, credito d'imposta…)
- **fondo perduto sì/no e in che percentuale**, importi e dotazione
- **riassunto** del bando
- **pertinenza 0–100** rispetto alla tua attività (descritta in `config.yaml`)

I risultati finiscono in `docs/`:

| File | Contenuto |
|---|---|
| `docs/index.html` | pagina consultabile con ricerca e filtri (pertinenza minima, solo nuovi, solo fondo perduto) |
| `docs/report.md` | report leggibile direttamente su GitHub |
| `docs/bandi.json` | dati strutturati |

I bandi vengono memorizzati in `data/bandi.db`: quelli comparsi oggi sono marcati **Nuovo**, quelli già
analizzati vengono rianalizzati solo se la pagina o gli allegati cambiano, quelli spariti dall'elenco
vengono considerati chiusi.

## Configurazione

Tutto si regola in `config.yaml`:

- `profilo`: descrivi la tua attività e i tuoi interessi (usato per calcolare la pertinenza);
- `fonti`: gli elenchi da controllare. Per aggiungerne uno basta un nuovo blocco con `nome` e `url`.
  I link ai bandi vengono riconosciuti automaticamente; se su un sito non funziona, indica
  `selettore_link` (CSS) o `regex_link` (espressione regolare sull'URL);
- `analisi`: modello Claude, limiti di testo e di allegati.

## Esecuzione automatica giornaliera (GitHub Actions)

Il workflow `.github/workflows/bandi-giornaliero.yml` gira ogni mattina e salva database e report nel repository.

1. In **Settings → Secrets and variables → Actions** aggiungi il secret `ANTHROPIC_API_KEY`
   (senza chiave l'app funziona lo stesso, ma con un'estrazione semplificata e senza riassunti).
2. Facoltativo – email con i bandi nuovi: aggiungi `SMTP_HOST` (es. `smtp.gmail.com`), `SMTP_PORT` (`587`),
   `SMTP_USER`, `SMTP_PASSWORD` (per Gmail una *password per le app*) ed `EMAIL_TO`.
3. Facoltativo – pagina web: in **Settings → Pages** pubblica la cartella `/docs` del branch principale.
4. Per una prima esecuzione immediata: **Actions → Monitor bandi giornaliero → Run workflow**.

## Esecuzione locale

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # facoltativo
python -m bandi_monitor             # tutte le fonti
python -m bandi_monitor --limite 2  # prova veloce su 2 bandi per fonte
python -m bandi_monitor --rianalizza
```

Test: `pip install pytest && pytest`.

## Costi

Ogni bando nuovo o modificato richiede una chiamata a Claude con il testo di pagina e allegati
(fino a `max_caratteri`, ~100.000 token). I bandi invariati non vengono rianalizzati, quindi dopo la
prima esecuzione il costo giornaliero dipende solo dai bandi nuovi. Per ridurre i costi puoi impostare
`modello: claude-sonnet-5-5` o `claude-haiku-5-5` in `config.yaml`.
