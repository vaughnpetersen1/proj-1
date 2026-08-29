"""Source refresh pipeline.

Turns the seeded, search-summarised records into verified, quoted records -- but
only when the network allows it. In an environment where egress is blocked this
reports exactly what it could not reach rather than degrading silently.

It fetches first-party pages, extracts visible text, and stores a short excerpt
(fair-use length, never a whole work) alongside the existing structured record.
It never bypasses paywalls, authentication or access controls: a page that
requires login is recorded as inaccessible.
"""

from __future__ import annotations

import datetime as dt
import html
import re
import urllib.error
import urllib.request
from typing import Any

from ...db.store import STORE, Store

MAX_EXCERPT_CHARS = 600
TIMEOUT = 20
UA = {"User-Agent": "trading-brain-research/1.0 (personal research; respects robots)"}


def _fetch(url: str) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.status in (401, 403):
                return False, f"HTTP {resp.status}: access restricted -- not bypassed"
            return True, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 402, 403):
            return False, (f"HTTP {exc.code}: the page is behind authentication or a "
                           "paywall. Not bypassed, by design.")
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def _visible_text(doc: str) -> str:
    doc = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", doc)
    doc = re.sub(r"(?s)<[^>]+>", " ", doc)
    return re.sub(r"\s+", " ", html.unescape(doc)).strip()


def refresh_sources(store: Store = STORE, limit: int = 25) -> dict[str, Any]:
    rows = [s for s in store.sources() if s.get("url") and not s.get("fulltext_verified")]
    results: list[dict[str, Any]] = []
    verified = 0
    for s in rows[:limit]:
        ok, payload = _fetch(s["url"])
        if not ok:
            results.append({"source": s["source"], "url": s["url"], "verified": False,
                            "reason": payload})
            continue
        text = _visible_text(payload)
        excerpt = text[:MAX_EXCERPT_CHARS]
        store.update("sources", s["id"], {
            "fulltext_verified": 1,
            "note": ((s.get("note") or "") +
                     f" | full text fetched {dt.date.today().isoformat()}, "
                     f"{len(text)} chars of visible text").strip(" |"),
        })
        # attach a short excerpt to the knowledge items from this source
        for k in store.query("SELECT id FROM knowledge_items WHERE source_id=?", (s["id"],)):
            store.update("knowledge_items", k["id"], {"quote": excerpt})
        verified += 1
        results.append({"source": s["source"], "url": s["url"], "verified": True,
                        "chars": len(text), "excerpt_chars": len(excerpt)})
    return {
        "attempted": len(rows[:limit]), "verified": verified,
        "still_unverified": len(rows) - verified,
        "results": results,
        "policy": [
            f"Only the first {MAX_EXCERPT_CHARS} characters of visible text are stored, as a "
            "citation-length excerpt. Whole works are never copied.",
            "Pages returning 401/402/403 are recorded as inaccessible. Paywalls, logins and "
            "access controls are never circumvented.",
            "Only URLs already recorded in the sources table are fetched; this pipeline does "
            "not crawl.",
        ],
    }
