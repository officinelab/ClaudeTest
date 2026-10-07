from __future__ import annotations

import time

import requests

USER_AGENT = "Mozilla/5.0 (compatible; BandiMonitor/0.1; +https://github.com/officinelab/ClaudeTest)"


class Fetcher:
    """Sessione HTTP con retry e una piccola pausa tra le richieste per non sovraccaricare i siti."""

    def __init__(self, delay: float = 1.0, timeout: float = 60.0, retries: int = 3):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "it-IT,it;q=0.9"})
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self._last = 0.0

    def get(self, url: str, max_bytes: int | None = None) -> requests.Response:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        last_exc: Exception | None = None
        for attempt in range(self.retries):
            try:
                resp = self.session.get(url, timeout=self.timeout, stream=max_bytes is not None)
                self._last = time.monotonic()
                resp.raise_for_status()
                if max_bytes is not None:
                    chunks, size = [], 0
                    for chunk in resp.iter_content(64 * 1024):
                        size += len(chunk)
                        if size > max_bytes:
                            resp.close()
                            raise ValueError(f"File troppo grande (> {max_bytes // 1_000_000} MB): {url}")
                        chunks.append(chunk)
                    resp._content = b"".join(chunks)
                if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
                    # Molti siti PA non dichiarano il charset: lascia decidere a requests.
                    resp.encoding = resp.apparent_encoding or resp.encoding
                return resp
            except ValueError:
                raise
            except requests.RequestException as exc:
                last_exc = exc
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and 400 <= status < 500 and status != 429:
                    break
                time.sleep(2 ** (attempt + 1))
        assert last_exc is not None
        raise last_exc
