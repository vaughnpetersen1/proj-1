"""Knowledge base: seeding, search, and the "why does the AI believe this?" trail."""

from __future__ import annotations

from typing import Any

from ..db.store import STORE, Store
from ..provenance import Claim, EvidenceClass, SourceRef
from .sources.seed_records import DISCOVERED_SOURCES, KNOWLEDGE

CATEGORIES = ("MARKET_REGIME", "SECTOR_THEME", "STOCK_SELECTION", "SETUP", "ENTRY",
              "RISK", "TRADE_MANAGEMENT")


def seed_knowledge_base(store: Store = STORE, force: bool = False) -> dict[str, Any]:
    """Idempotent. Re-running adds nothing unless ``force`` clears first."""
    if force:
        store.execute("DELETE FROM knowledge_items")
        store.execute("DELETE FROM sources")
    before = len(store.knowledge(limit=10_000))
    src_ids: dict[str, int] = {}
    for item in KNOWLEDGE:
        s = item["src"]
        key = f"{s['source']}|{s.get('url')}|{s.get('title')}"
        if key not in src_ids:
            src_ids[key] = store.add_source(
                source=s["source"], url=s.get("url"), author=s.get("author"),
                published=s.get("date"), title=s.get("title"),
                source_type=s.get("source_type"), topic=item["category"],
                retrieval_method=s.get("retrieval_method"),
                fulltext_verified=s.get("fulltext_verified", False), note=s.get("note"))
        existing = store.one(
            "SELECT id FROM knowledge_items WHERE source_id=? AND concept=?",
            (src_ids[key], item["concept"]))
        if existing:
            continue
        store.add_knowledge(
            source_id=src_ids[key], category=item["category"], concept=item["concept"],
            observation=item["observation"], quote=None,
            summary=item["observation"][:280],
            rule_text=item.get("rule_text"),
            explicit=item.get("explicit", False),
            quantitative_interpretation=item.get("quant", []),
            backtest_hypothesis=item.get("hypothesis"),
            related_component=item.get("component"),
            evidence_class=EvidenceClass.SOURCE_FACT.value,
            confidence_note=("Paraphrase of what search results attribute to the source. "
                             "The first-party page was not fetched in full in this "
                             "environment, so no verbatim quote is stored."
                             if s.get("retrieval_method") == "web_search_summary" else
                             "Supplied directly by the operator."))
    for d in DISCOVERED_SOURCES:
        store.add_source(source=d["source"], url=d.get("url"), author=d.get("author"),
                         title=d.get("title"), source_type=d.get("source_type"),
                         topic=d.get("topic"),
                         retrieval_method=d.get("retrieval_method"),
                         fulltext_verified=False, note=d.get("note"))
    after = len(store.knowledge(limit=10_000))
    store.audit("system", "seed_knowledge_base", {"added": after - before})
    return {"sources": len(store.sources()), "knowledge_items": after,
            "added": after - before}


def search_knowledge(q: str | None = None, category: str | None = None,
                     store: Store = STORE) -> list[dict[str, Any]]:
    rows = store.knowledge(category=category, q=q)
    for r in rows:
        r["unverified"] = not bool(r.get("fulltext_verified"))
    return rows


def knowledge_stats(store: Store = STORE) -> dict[str, Any]:
    rows = store.knowledge(limit=10_000)
    by_cat: dict[str, int] = {}
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    srcs = store.sources()
    return {
        "items": len(rows),
        "by_category": by_cat,
        "explicit_rules": sum(1 for r in rows if r["explicit"]),
        "inferred_rules": sum(1 for r in rows if not r["explicit"]),
        "sources": len(srcs),
        "sources_fulltext_verified": sum(1 for s in srcs if s["fulltext_verified"]),
        "sources_unverified": sum(1 for s in srcs if not s["fulltext_verified"]),
        "candidate_definitions": sum(len(r.get("quantitative_interpretation") or [])
                                     for r in rows),
        "verification_note": (
            "Sources marked unverified were summarised from web search results because this "
            "environment blocks outbound HTTPS to non-package hosts. No verbatim quotes are "
            "stored for them. Run `research refresh` with network access to verify."),
    }


def why_rule(concept: str, store: Store = STORE) -> dict[str, Any]:
    """The audit trail behind a rule: SOURCE -> interpretation -> hypothesis -> evidence."""
    rows = store.knowledge(q=concept, limit=50)
    rows = [r for r in rows if concept.lower() in (r["concept"] or "").lower()] or rows
    if not rows:
        return {"found": False, "concept": concept,
                "reason": f"nothing in the knowledge base mentions {concept!r}"}

    chains = []
    for r in rows:
        exps = store.query(
            "SELECT e.id, e.name, e.verdict, e.conclusion, e.sample_size, e.created_at, "
            "e.data_origin, h.question FROM experiments e "
            "LEFT JOIN hypotheses h ON h.id = e.hypothesis_id "
            "WHERE e.name LIKE ? OR h.question LIKE ? ORDER BY e.id DESC LIMIT 5",
            (f"%{r['concept']}%", f"%{r['concept']}%"))
        chains.append({
            "concept": r["concept"],
            "category": r["category"],
            "source": {"name": r["source"], "url": r["url"], "author": r["author"],
                       "title": r["title"], "date": r["published"],
                       "retrieval_method": r["retrieval_method"],
                       "fulltext_verified": bool(r["fulltext_verified"])},
            "step_1_source_says": r["observation"],
            "step_1_verbatim_quote": r["quote"],
            "step_2_rule_implied": r["rule_text"],
            "step_2_explicit_or_inferred": "EXPLICIT" if r["explicit"] else "INFERRED",
            "step_3_quantitative_candidates": r.get("quantitative_interpretation") or [],
            "step_4_backtest_hypothesis": r["backtest_hypothesis"],
            "step_5_evidence": exps or [],
            "step_5_note": ("No experiment has tested this yet. Until one has, the rule has "
                            "the status of a source claim, not a finding."
                            if not exps else None),
            "implemented_at": r["related_component"],
            "evidence_class": r["evidence_class"],
        })
    return {"found": True, "concept": concept, "chains": chains,
            "claim": Claim(
                statement=f"Provenance chain for '{concept}': "
                          f"{len(chains)} knowledge item(s) across "
                          f"{len({c['source']['name'] for c in chains})} source(s).",
                evidence=EvidenceClass.SOURCE_FACT,
                sources=[SourceRef(source=c["source"]["name"], url=c["source"]["url"],
                                   author=c["source"]["author"], title=c["source"]["title"],
                                   retrieval_method=c["source"]["retrieval_method"],
                                   fulltext_verified=c["source"]["fulltext_verified"])
                         for c in chains],
                methodology="Direct lookup in the knowledge base; no inference applied.",
            ).to_dict()}
