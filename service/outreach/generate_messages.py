#!/usr/bin/env python3
"""Generate personalized outreach messages for every lead in found_leads.csv.

Merges each found lead into the right niche template (human voice, per house
rules) and writes them all to outbox.md — you verify the lead, copy, send.

Usage:
    python3 generate_messages.py                    # all unverified found leads
    python3 generate_messages.py --demo-base https://blankd.online
"""

import argparse
import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FOUND = os.path.join(HERE, "found_leads.csv")
OUTBOX = os.path.join(HERE, "outbox.md")

# niche keyword -> (demo path, template)
TEMPLATES = {
    "barber": ("/demos/barber.html",
        "Hey, quick one. I was looking up barbershops around {city} and realized "
        "{name} doesn't have a website, so anyone googling \"barber near me\" is "
        "finding other shops first.\n\nI build websites for barbershops. $300 flat, "
        "done in 48 hours. Here's my work: {link}\n\nWant me to mock up a {name} "
        "version for free? No catch, you'd just get to see it."),
    "salon": ("/demos/barber.html",
        "Hi! Found {name} looking at salons around {city}. Great reviews but no "
        "website, so people googling \"salon near me\" find the chains first.\n\n"
        "I build websites for salons. $300 flat, done in 48 hours, with a book-now "
        "button that goes wherever you want. My work: {link}\n\nWant a free {name} "
        "mockup? No commitment, you'd just get to see it."),
    "detail": ("/demos/detailing.html",
        "Hey, saw {name}'s work — it speaks for itself. But there's no website when "
        "people google detailing around {city}, so shops with sites are getting "
        "customers you're better than.\n\nI build sites for detailers. $300 flat, 48 "
        "hours. Packages, before/afters, and a \"text me a photo for a quote\" "
        "button. Example: {link}\n\nWant a free {name} mockup? If you don't like it "
        "you've lost nothing."),
    "lawn": ("/demos/landscaping.html",
        "Hey, found {name} looking at lawn care around {city}. Solid operation but "
        "no website, and the companies that have one are getting everyone who "
        "googles instead of scrolling Facebook.\n\nI build sites for lawn companies. "
        "$300 flat, 48 hours, with a \"text us your yard for a quote\" button. "
        "Example: {link}\n\nWant me to mock one up for {name} free so you can see "
        "it first?"),
    "landscap": ("/demos/landscaping.html", None),   # alias -> lawn template
    "handyman": ("/demos/landscaping.html",
        "Hey, found {name} on the map. Quick thought from a web guy — when "
        "somebody's faucet is leaking they google \"handyman near me\", they don't "
        "scroll Facebook. Right now that search sends them to Angi ads and the guys "
        "with websites.\n\nI build sites for trades guys. $300 flat, 48 hours, with "
        "a \"text me a photo of the job\" button. Example: {link}\n\nWant a free "
        "{name} mockup? No catch."),
    "restaurant": ("/demos/taqueria.html",
        "Hey! Found {name} — reviews look great, but there's no website with your "
        "menu when people search around {city}.\n\nI build restaurant sites. $300 "
        "flat, menu-first, done in 48 hours. Look: {link}\n\nWant a free {name} "
        "mockup? The menu alone will make people hungry."),
}
TEMPLATES["landscap"] = TEMPLATES["lawn"]


def pick_template(query: str):
    q = query.lower()
    for key, tpl in TEMPLATES.items():
        if key in q:
            return tpl
    return TEMPLATES["handyman"]  # generic trades fallback


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--demo-base", default="https://blankd.online",
                   help="base URL for demo links (default blankd.online)")
    args = p.parse_args()

    if not os.path.exists(FOUND):
        raise SystemExit("No found_leads.csv yet — run leadfinder.py first.")

    with open(FOUND, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit("found_leads.csv is empty.")

    out = ["# Outbox — generated messages (VERIFY each lead before sending)\n"]
    for r in rows:
        path, tpl = pick_template(r["query"])
        city = r["query"].split(" in ")[-1] if " in " in r["query"] else r["address"]
        msg = tpl.format(name=r["business"], city=city, link=args.demo_base + path)
        out.append(f"\n---\n\n### {r['business']} — {r['phone'] or 'find contact on FB/Maps'}")
        out.append(f"{r['address']} · {r['rating']}★ ({r['reviews']} reviews) · found {r['date_found']}")
        out.append(f"**VERIFY FIRST** (30-sec google: \"{r['business']}\" + website)\n")
        out.append("> " + msg.replace("\n", "\n> "))

    with open(OUTBOX, "w") as f:
        f.write("\n".join(out) + "\n")
    print(f"Wrote {len(rows)} messages to outbox.md — verify, copy, send.")


if __name__ == "__main__":
    main()
