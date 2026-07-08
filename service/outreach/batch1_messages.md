# Batch 1 — Ready-to-send messages (Minneapolis + Brainerd)

**Voice rule (boss's note, 2026-07-08):** these read like a text from a real
person, not a marketing email. Short sentences. No perfect corporate polish.
Clean and to the point. If a line sounds like a brochure, cut it.

19 leads loaded in `leads.csv`. The 3 marked **VERIFIED** are safe to send today.
The rest need a 30-second Google ("business name + city") to confirm no website —
search results can be stale, and telling someone with a site that they don't have
one burns the lead.

**Before sending:** deploy the landing page + demos (see `service/README.md`) so
`[demo link]` below is a real URL. After each send: set the lead's status to
`contacted` in leads.csv and update `last_touch`.

---

## ✅ VERIFIED — send these today

### 1. Twiins BarberShop (Minneapolis — FB DM)

> Hey, quick one. I was looking up barbershops in south Minneapolis and
> realized you guys don't have a website, just the FB page. So anyone
> googling "barber near me" around 38th is finding other shops first.
>
> I build websites for barbershops. $300 flat, done in 48 hours.
> Here's my work: [demo link]
>
> Honestly I already started putting a Twiins version together because your
> before/after posts are exactly the kind of thing that should be on a
> website. Want me to send it over? Free to look, no catch.

*(True statement — the mockup exists at `service/clients/twiins-barbershop/`.
Once his photos are in, send the screenshot WITH this message.)*

### 2. Xtreme Lawn Care LLC (Baxter — FB DM or text (218) 851-2589)

> Hey, found your page looking at lawn care around Baxter. You guys do
> everything from grass to snow but there's no website, and Elevated and
> Supreme Lawn both have one. So they're getting everyone who googles
> instead of scrolling Facebook.
>
> I build sites for lawn companies. $300 flat, done in 48 hours. One page
> with your services, your plans, and a "text us your yard for a quote"
> button. Here's an example: [demo link]
>
> Want me to mock one up for Xtreme for free so you can see it first?
> If it's not for you, no worries.

*(Note: this one has real teeth — their direct competitors DO have websites.)*

### 3. Cut Right Lawn Care (Brainerd — FB DM to CutRightLC, or email Kaylarauen@gmail.com) — UPGRADE pitch

> Hey Kayla, found Cut Right while poking around lawn care pages in Brainerd.
> Not sure if it's on your radar but your website is still on Jobber's domain
> (the jobbersites.com link). It works, but it looks like every other Jobber
> template and the address isn't even yours.
>
> I build custom sites for lawn companies. $300 flat, on your own domain,
> done in about 2 days. All your Jobber booking links keep working, the new
> site just gets more people to click them.
>
> Here's one I did for a landscaping company: [demo link]
>
> If you're even a little curious I'll mock up a Cut Right homepage for free
> so you can actually see it before deciding anything. No pressure either way.

---

## 🔍 VERIFY FIRST, then send (30-sec Google each)

**Brainerd barbers** — The Men's Depot, The 512 Studio, Shear Innovations:
use the Twiins template above, swap the shop name and street detail. Brainerd
angle: *"Blue Ox and Mr. Oscar both have websites — the shops without one are
invisible when tourists Google 'barber near me' from the lake."*

**Brainerd lawn/landscape** — Grace Lawn Care, SS Lawn & Landscape, Man Made
Landscaping, JN Tree Service: use the Xtreme template. For JN Tree, lead with
the arborist certification: *"you're ISA-certified and Google can't tell —
that belongs at the top of a website."*

**El Potro / El Paisa (Brainerd Mexican food)** — taqueria demo is the closer:
> Hola! Found El Potro on 7th St — great reviews, but no website with your menu
> when people search "Mexican food Brainerd." I build restaurant sites: $300
> flat, menu-first, live in 48 hours. Look at this one and tell me it wouldn't
> sell some birria: [taqueria demo link]

**Minneapolis barbers** — Stilo Cuts, Hair Lounge, One 21 4 East: Twiins
template, swap neighborhood ("Uptown" / "Washington Ave").

**Minneapolis mobile detailers** — Efficient, Twin Cities MD, Shine Time,
Liam's: use the detailing demo:
> Saw your detailing work on Facebook — results speak for themselves, but
> there's no site for people to see packages and prices when they Google
> mobile detailing. I build detailer sites: $300 flat, 48 hours, package
> pricing + before/afters + "text a photo for a quote." Example: [demo link]

---

## The rhythm

- 3 verified sends today. Then verify + send 3–4/day until the list is empty.
- Follow-ups: run `python3 service/outreach/pipeline.py` daily — it tells you who's due.
- **Anyone replies with interest → tell me the business immediately.** I'll have a
  personalized mockup ready the same day. That's the close.

---

## ✅ VERIFIED #4 — Eden Landscape (Brainerd — FB DM to Skylar, or text (218) 650-0056)

*Boss-sourced lead, verified 2026-07-08: no website — FB page, Nextdoor, Yelp stub,
and a Chamber directory listing only. Owner is Skylar. High-ticket hardscape work
(patios, retaining walls, fire pits) + winter pivot to TILE/LVP/hardwood flooring.
Angle: his jobs are $5k+, his "Call Us Today" posts show he wants leads, and a
website works his pitch 24/7 — including the winter flooring switch his FB bio
has to explain in a paragraph.*

> Hey Skylar! Came across Eden Landscape on Facebook — that paver walkway you
> posted this week is clean work. Noticed something though: when people Google
> "hardscape Brainerd," you don't show up with a website — Yardcreations and
> Landsburg do, and they're pulling the searchers your posts never reach.
>
> I build sites for landscape companies: $300 flat, live in 48 hours. Yours
> would show the patio/wall/fire-pit work front and center, and switch to
> pushing your tile & LVP flooring every winter — same site, both seasons,
> so you're never re-explaining the pivot in a Facebook bio.
>
> Here's my work for a landscaper: [demo link]
>
> Want me to mock up an Eden Landscape version with your actual project photos?
> Free — if you don't love it, you keep the mockup and I disappear.
