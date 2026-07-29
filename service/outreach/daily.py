#!/usr/bin/env python3
"""Daily send batch — turns leads.csv into today's ready-to-send messages.

Boss hops on, runs this (or asks Claude to), and gets N short DMs ready to
copy-paste. No hunting, no writing, no deciding who to contact.

Messages follow the SHORT-DMS rule (boss-set 2026-07-24): message 1 is ONE
question, under 25 words, no link, no price, no pitch. Three people called
our old 70-word DMs automated; that was the #1 conversion blocker.

Usage:
    python3 daily.py                 # 10 messages
    python3 daily.py --count 20      # more
    python3 daily.py --niche barber  # only barbers

Stdlib only. Reads leads.csv, writes TODAY-SEND.md. Never sends anything —
the boss's thumb stays on the button (house rule 5).
"""

import argparse
import csv
import os
import re
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
LEADS = os.path.join(HERE, "leads.csv")
OUT = os.path.join(HERE, "TODAY-SEND.md")

SITE = "https://vaughnpetersen1.github.io/blankd-v2"

# Leads that already have a real mockup built -> much stronger opener.
# key: lowercase substring of business name -> page slug
HAS_MOCKUP = {
    "512 studio": "clients/512.html",
    "mg detailing": "clients/mgdetailing.html",
    "mn detail": "clients/mndetail.html",
    "kz's": "clients/kzs.html",
    "icuttz": "clients/icuttz.html",
    "larry's": "clients/larrys.html",
    "birdy's": "clients/birdys.html",
    "twiins": "clients/twiins.html",
    "eden": "clients/eden.html",
}

# niche -> what to call them in a sentence
NICHE_WORD = {
    "barber": "barbers",
    "salon": "salons",
    "detailing": "detailers",
    "landscaping": "landscapers",
    "lawncare": "lawn guys",
    "hardscape/landscaping": "landscapers",
    "handyman": "handymen",
    "foodtruck": "food trucks",
}


def mockup_for(name: str):
    low = name.lower()
    for key, slug in HAS_MOCKUP.items():
        if key in low:
            return f"{SITE}/{slug}"
    return None


def first_name(contact: str):
    """Pull a human first name out of the notes/contact if one is obvious."""
    m = re.search(r"\(([A-Z][a-z]+)\s+[A-Z][a-z]+\)", contact)
    return m.group(1) if m else None


def clean_name(name: str):
    """Say it like a person would out loud — nobody texts 'LLC'."""
    n = re.sub(r"\s*[,.]?\s*\b(LLC|L\.L\.C\.|Inc|Inc\.|Co\.|Corp|Ltd)\b\.?", "", name, flags=re.I)
    n = re.sub(r"\s*\([^)]*\)", "", n)   # drop parenthetical owner names
    return n.strip(" .,-")


def opener(name: str, niche: str, contact: str, has_mockup: bool):
    """ONE question, under 25 words, no link, no price. See SHORT-DMS.md."""
    who = first_name(contact)
    hey = f"hey {who}" if who else "hey"
    name = clean_name(name)

    if has_mockup:
        # Strongest line we have: the thing already exists.
        return f"{hey}, made you something for {name}. mind if I send a link?"

    # Cold: just ask the question. Nothing else.
    variants = [
        f"{hey} is this {name}? quick question, do you guys have a website? couldn't find one",
        f"{hey}, quick q. does {name} have a website anywhere? couldn't find one",
        f"{hey}! do you guys have a website? only found the facebook page",
    ]
    # vary by name length so consecutive sends don't look identical
    return variants[len(name) % len(variants)]


def load_leads(niche_filter=None):
    rows = []
    with open(LEADS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("status") or "").strip() != "lead":
                continue
            if (r.get("last_touch") or "").strip():
                continue  # already touched
            if niche_filter and niche_filter.lower() not in (r.get("niche") or "").lower():
                continue
            rows.append(r)
    # warm ones (mockup already built) go first — highest chance of a reply
    rows.sort(key=lambda r: (mockup_for(r["business"]) is None, r["business"]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--niche", default=None)
    args = ap.parse_args()

    rows = load_leads(args.niche)[: args.count]
    if not rows:
        print("No untouched leads. Ask Claude for a new batch.")
        return

    lines = [
        f"# TODAY'S SEND LIST — {date.today()}",
        "",
        f"{len(rows)} messages. Copy, send, move on. Do NOT double-text —",
        "one message each, then wait for a reply.",
        "",
        "When someone replies, paste it to Claude and he builds/sends the next step.",
        "",
    ]

    for i, r in enumerate(rows, 1):
        name = r["business"]
        link = mockup_for(name)
        msg = opener(name, r.get("niche", ""), r.get("contact", ""), link is not None)
        lines += [
            "=" * 56,
            f"{i}) {name.upper()}  ·  {r.get('city','')}",
            f"   {r.get('contact','')}",
        ]
        if link:
            lines.append(f"   SITE ALREADY BUILT -> {link}")
            lines.append("   (if they say yes, send that link)")
        lines += ["=" * 56, "", f"> {msg}", ""]

    lines += [
        "=" * 56,
        "IF THEY REPLY 'no we don't have one':",
        "  - mockup exists ->  figured. I actually already built you one to show you. want the link?",
        "  - no mockup yet ->  figured. I build them for [niche]. want me to make you one free so you can see it? no catch",
        "",
        "IF THEY ASK HOW MUCH:",
        "  $300 flat, live in 2 days. half now half when you love it",
        "=" * 56,
    ]

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    warm = sum(1 for r in rows if mockup_for(r["business"]))
    print(f"Wrote {len(rows)} messages to {os.path.basename(OUT)} "
          f"({warm} with a site already built)")
    print("\n".join(lines[:0]))  # keep stdout clean


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
