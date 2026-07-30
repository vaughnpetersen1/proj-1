#!/usr/bin/env python3
"""Blankd site generator — premium client mockups from a small config.

Why this exists: hand-writing 400 lines of HTML per lead is slow, and the
boss (rightly) called the fast template-y versions low quality. This builds
every site to the SAME premium standard set by the MG Detailing rebuild:
editorial Fraunces/Inter type, framed hero + stat badge, priced menu with
leader dots, gallery, about split, reviews, dark contact block, scroll
reveals as progressive enhancement.

Usage:
    python3 sitegen.py                # build every site in SITES
    python3 sitegen.py larrys birdys  # build just these

Add a new client = add a dict to SITES. Takes about 2 minutes.
House rules honored: no emojis, inline SVG icons only, placeholder prices and
photos clearly marked as placeholders on-page.
"""

import os
import sys

OUT_DIR = "/workspace/blankd-v2/clients"

ICONS = {
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "star": '<path d="m12 2 2.9 6.3 6.8.8-5 4.6 1.3 6.8L12 17.3 6 20.5l1.3-6.8-5-4.6 6.8-.8z"/>',
    "shield": '<path d="M12 2 4 6v6c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "pin": '<path d="M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 0 1 16 0z"/><circle cx="12" cy="10" r="3"/>',
    "phone": '<path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3A19.5 19.5 0 0 1 5.1 13 19.8 19.8 0 0 1 2 4.2 2 2 0 0 1 4 2h3a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L8 10a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.5c1 .4 2 .6 3 .7a2 2 0 0 1 1.6 2z"/>',
    "mail": '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-10 6L2 7"/>',
    "cal": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 2v4M16 2v4M3 10h18"/>',
    "car": '<path d="M3 12h13l3-4h2v6H3z"/><circle cx="7" cy="17" r="2"/><circle cx="17" cy="17" r="2"/>',
    "boat": '<path d="M3 15h18l-2 5H5z"/><path d="M6 15V6l9 3-9 6"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
}


def icon(name, cls="ic", style=""):
    s = f' style="{style}"' if style else ""
    return f'<svg class="{cls}" viewBox="0 0 24 24"{s}>{ICONS[name]}</svg>'


def css(t):
    """t = theme dict (bg, ink, accent, ...). Dark hero optional."""
    return f"""
  :root{{
    --bg:{t['bg']}; --ink:{t['ink']}; --soft:{t['soft']}; --faint:{t['faint']};
    --line:{t['line']}; --panel:{t['panel']}; --accent:{t['accent']};
    --shadow:0 22px 60px -28px rgba(20,18,14,.35); --r:16px;
  }}
  *{{margin:0;padding:0;box-sizing:border-box}}
  html{{scroll-behavior:smooth}}
  body{{font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);color:var(--ink);line-height:1.65;-webkit-font-smoothing:antialiased}}
  h1,h2,h3{{font-family:'Fraunces',Georgia,serif}}
  .wrap{{max-width:1120px;margin:0 auto;padding:0 28px}}
  a{{color:inherit}}
  .ic{{width:20px;height:20px;stroke:currentColor;fill:none;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}}
  .eyebrow{{font-size:.74rem;font-weight:600;letter-spacing:.24em;text-transform:uppercase;color:var(--accent)}}
  .js .reveal{{opacity:0;transform:translateY(22px);transition:opacity .7s cubic-bezier(.2,.7,.2,1),transform .7s cubic-bezier(.2,.7,.2,1)}}
  .js .reveal.in{{opacity:1;transform:none}}
  @media (prefers-reduced-motion:reduce){{.js .reveal{{opacity:1;transform:none;transition:none}}}}

  header{{position:sticky;top:0;z-index:50;background:{t['hdr_bg']};backdrop-filter:blur(14px);border-bottom:1px solid {t['hdr_line']}}}
  nav{{display:flex;align-items:center;justify-content:space-between;height:70px}}
  .brand{{font-family:'Fraunces',serif;font-weight:600;font-size:1.12rem;color:{t['hdr_text']};text-decoration:none}}
  .navlinks{{display:flex;gap:32px;list-style:none}}
  .navlinks a{{text-decoration:none;font-size:.92rem;font-weight:500;color:{t['hdr_link']};transition:color .2s}}
  .navlinks a:hover{{color:{t['hdr_text']}}}
  .nav-cta{{text-decoration:none;background:{t['cta_bg']};color:{t['cta_text']};padding:11px 22px;border-radius:100px;font-size:.88rem;font-weight:700;transition:transform .2s}}
  .nav-cta:hover{{transform:translateY(-1px)}}
  @media(max-width:860px){{.navlinks{{display:none}}}}

  .hero{{background:{t['hero_bg']};color:{t['hero_text']};padding:84px 0 76px;position:relative;overflow:hidden}}
  .hero:after{{content:"";position:absolute;inset:0;background:radial-gradient(ellipse at 70% 18%,{t['hero_glow']},transparent 62%);pointer-events:none}}
  .hero-grid{{display:grid;grid-template-columns:1.05fr .95fr;gap:56px;align-items:center;position:relative;z-index:1}}
  .hero h1{{font-size:clamp(2.7rem,5.7vw,4.4rem);line-height:1.02;font-weight:600;letter-spacing:-.02em;margin:18px 0 0;color:{t['hero_head']}}}
  .hero h1 em{{font-style:italic;color:var(--accent)}}
  .hero p.lede{{font-size:1.14rem;color:{t['hero_soft']};max-width:450px;margin:22px 0 0}}
  .hero-cta{{display:flex;gap:14px;flex-wrap:wrap;margin-top:32px}}
  .btn{{display:inline-flex;align-items:center;gap:9px;text-decoration:none;font-weight:600;font-size:.96rem;padding:15px 30px;border-radius:100px;transition:transform .2s,box-shadow .2s}}
  .btn-primary{{background:{t['cta_bg']};color:{t['cta_text']};font-weight:700}}
  .btn-primary:hover{{transform:translateY(-2px);box-shadow:0 14px 30px -14px rgba(0,0,0,.5)}}
  .btn-ghost{{background:transparent;color:{t['hero_text']};border:1.5px solid {t['ghost_line']}}}
  .btn-ghost:hover{{border-color:var(--accent);color:var(--accent)}}
  .hero-note{{margin-top:20px;font-size:.86rem;color:{t['hero_faint']};display:flex;align-items:center;gap:8px}}
  .hero-art{{position:relative}}
  .hero-art .frame{{border-radius:var(--r);overflow:hidden;box-shadow:var(--shadow);border:1px solid {t['frame_line']};aspect-ratio:4/5;background:{t['frame_bg']};display:flex;align-items:center;justify-content:center;text-align:center;color:{t['frame_text']};font-size:.86rem;padding:26px}}
  .hero-art .frame img{{width:100%;height:100%;object-fit:cover}}
  .hero-badge{{position:absolute;left:-22px;bottom:-22px;background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px 20px;box-shadow:var(--shadow);display:flex;align-items:center;gap:13px;color:var(--ink)}}
  .hero-badge .num{{font-family:'Fraunces',serif;font-size:1.7rem;font-weight:600;line-height:1}}
  .hero-badge .lbl{{font-size:.76rem;color:var(--soft);line-height:1.3}}
  @media(max-width:860px){{.hero-grid{{grid-template-columns:1fr;gap:44px}}.hero-art{{max-width:400px;margin:0 auto}}.hero-badge{{left:0}}}}

  .trust{{border-bottom:1px solid var(--line);background:#fff}}
  .trust .wrap{{display:flex;flex-wrap:wrap;gap:14px 44px;justify-content:center;padding:24px 28px}}
  .trust .item{{display:flex;align-items:center;gap:10px;font-size:.94rem;color:var(--soft)}}
  .trust .item svg{{color:var(--accent)}}
  .trust .item b{{color:var(--ink);font-weight:600}}

  section{{padding:92px 0}}
  .sec-head{{max-width:620px;margin:0 auto 52px;text-align:center}}
  .sec-head h2{{font-size:clamp(2rem,4vw,2.8rem);font-weight:600;letter-spacing:-.02em;line-height:1.08;margin-top:12px}}
  .sec-head p{{color:var(--soft);margin-top:14px;font-size:1.05rem}}

  .menu{{display:grid;grid-template-columns:1fr 1fr;gap:0 60px;max-width:880px;margin:0 auto}}
  .row{{display:flex;align-items:baseline;gap:14px;padding:17px 2px;border-bottom:1px solid var(--line)}}
  .row .n{{font-weight:600;white-space:nowrap}}
  .row .dots{{flex:1;border-bottom:1px dotted #d8d1c2;transform:translateY(-4px)}}
  .row .p{{font-family:'Fraunces',serif;font-weight:600;font-size:1.2rem;white-space:nowrap}}
  .row .d{{display:block;font-family:'Inter',sans-serif;font-size:.82rem;color:var(--faint);font-weight:400;margin-top:2px;white-space:normal}}
  @media(max-width:860px){{.menu{{grid-template-columns:1fr}}}}

  .gallery{{display:grid;grid-template-columns:repeat(4,1fr);grid-auto-rows:200px;gap:16px}}
  .tile{{border-radius:var(--r);border:1px dashed var(--line);background:{t['tile_bg']};display:flex;align-items:center;justify-content:center;text-align:center;color:{t['frame_text']};font-size:.84rem;padding:18px}}
  .tile.big{{grid-column:span 2;grid-row:span 2}}
  @media(max-width:860px){{.gallery{{grid-template-columns:repeat(2,1fr);grid-auto-rows:150px}}.tile.big{{grid-column:span 2;grid-row:span 1}}}}

  .about{{background:var(--panel)}}
  .about-grid{{display:grid;grid-template-columns:.9fr 1.1fr;gap:56px;align-items:center}}
  .about-art{{border-radius:var(--r);border:1px dashed var(--line);aspect-ratio:1/1;background:{t['tile_bg']};display:flex;align-items:center;justify-content:center;text-align:center;color:{t['frame_text']};font-size:.86rem;padding:26px}}
  .about h2{{font-size:clamp(1.9rem,3.6vw,2.6rem);font-weight:600;letter-spacing:-.02em;line-height:1.1;margin:12px 0 0}}
  .about p{{color:var(--soft);margin-top:18px;font-size:1.04rem}}
  .about .stats{{display:flex;gap:40px;margin-top:30px;flex-wrap:wrap}}
  .about .stat .n{{font-family:'Fraunces',serif;font-size:2rem;font-weight:600;line-height:1}}
  .about .stat .l{{font-size:.84rem;color:var(--faint);margin-top:4px}}
  @media(max-width:860px){{.about-grid{{grid-template-columns:1fr;gap:36px}}}}

  .quotes{{display:grid;grid-template-columns:repeat(3,1fr);gap:22px}}
  .quote{{background:#fff;border:1px solid var(--line);border-radius:var(--r);padding:30px}}
  .quote .stars{{color:var(--accent);letter-spacing:3px;font-size:.9rem}}
  .quote p{{margin:16px 0 20px;font-size:1.02rem;font-family:'Fraunces',serif;line-height:1.5}}
  .quote .who{{font-size:.86rem;color:var(--faint)}}
  @media(max-width:860px){{.quotes{{grid-template-columns:1fr}}}}

  .visit{{background:var(--ink);color:#fff}}
  .visit-grid{{display:grid;grid-template-columns:1fr 1fr;gap:56px}}
  .visit h2{{font-size:clamp(2rem,4vw,2.8rem);font-weight:600;letter-spacing:-.02em;color:#fff;line-height:1.06}}
  .visit .eyebrow{{color:var(--accent)}}
  .visit p.sub{{color:#a8a49b;margin-top:14px;font-size:1.04rem;max-width:400px}}
  .visit .lines{{margin-top:32px;display:flex;flex-direction:column;gap:17px}}
  .visit .line{{display:flex;align-items:center;gap:14px;color:#e9e7e2;text-decoration:none;font-size:1rem}}
  .visit .line svg{{color:var(--accent);flex:none}}
  .hours{{background:#fff;border-radius:var(--r);padding:30px;color:var(--ink)}}
  .hours h3{{font-size:1.25rem;font-weight:600;margin-bottom:16px}}
  .hours table{{width:100%;border-collapse:collapse;font-size:.96rem}}
  .hours td{{padding:11px 0;border-bottom:1px solid var(--line)}}
  .hours tr:last-child td{{border:none}}
  .hours td:last-child{{text-align:right;font-weight:600}}
  .hours .fine{{font-size:.78rem;color:var(--faint);margin-top:14px}}
  @media(max-width:860px){{.visit-grid{{grid-template-columns:1fr;gap:36px}}}}

  .placeholder-note{{text-align:center;color:var(--faint);font-size:.82rem;margin-top:26px}}
  footer{{background:var(--ink);color:#8a8479;padding:40px 0;border-top:1px solid rgba(255,255,255,.08);text-align:center;font-size:.86rem}}
  footer .fbrand{{font-family:'Fraunces',serif;color:#fff;font-size:1.2rem;margin-bottom:6px}}
  footer a{{color:var(--accent);text-decoration:none}}
"""


LIGHT = dict(bg="#faf8f5", ink="#17150f", soft="#5f5a51", faint="#8b857a",
             line="#e8e3d9", panel="#f2eee6", accent="#a8763c",
             hdr_bg="rgba(250,248,245,.88)", hdr_line="var(--line)",
             hdr_text="#17150f", hdr_link="#5f5a51",
             cta_bg="#17150f", cta_text="#fff",
             hero_bg="transparent", hero_text="#17150f", hero_head="#17150f",
             hero_soft="#5f5a51", hero_faint="#8b857a",
             hero_glow="rgba(168,118,60,.10)", ghost_line="#e8e3d9",
             frame_line="#e8e3d9", frame_bg="linear-gradient(155deg,#efe8dc,#e2d8c6)",
             frame_text="#a3977f", tile_bg="linear-gradient(150deg,#f0e9dd,#e4dac8)")

DARK = dict(bg="#f8f6f3", ink="#15130f", soft="#5c5850", faint="#8a8479",
            line="#e6e1d7", panel="#f1ece3", accent="#96702f",
            hdr_bg="rgba(21,19,15,.93)", hdr_line="rgba(255,255,255,.07)",
            hdr_text="#f4f1eb", hdr_link="#a9a49a",
            cta_bg="#96702f", cta_text="#15130f",
            hero_bg="#15130f", hero_text="#f4f1eb", hero_head="#fff",
            hero_soft="#b6b0a5", hero_faint="#8f8a80",
            hero_glow="rgba(150,112,47,.22)", ghost_line="rgba(255,255,255,.2)",
            frame_line="rgba(255,255,255,.12)",
            frame_bg="linear-gradient(155deg,#26221b,#1b1814)",
            frame_text="#6f6a60", tile_bg="linear-gradient(150deg,#efe9dd,#e2dac9)")

COOL = dict(bg="#fbfbfc", ink="#14171c", soft="#5e626a", faint="#8a8e96",
            line="#e6e8ec", panel="#f4f6f8", accent="#0f8a86",
            hdr_bg="rgba(251,251,252,.9)", hdr_line="var(--line)",
            hdr_text="#14171c", hdr_link="#5e626a",
            cta_bg="#0d1013", cta_text="#fff",
            hero_bg="transparent", hero_text="#14171c", hero_head="#14171c",
            hero_soft="#5e626a", hero_faint="#8a8e96",
            hero_glow="rgba(15,138,134,.10)", ghost_line="#e6e8ec",
            frame_line="#e6e8ec", frame_bg="linear-gradient(155deg,#e8eef0,#d8e2e5)",
            frame_text="#8fa0a4", tile_bg="linear-gradient(150deg,#eaf0f1,#dbe5e7)")

THEMES = {"light": LIGHT, "dark": DARK, "cool": COOL}


def build(cfg):
    t = THEMES[cfg.get("theme", "light")]
    navlinks = "".join(f'<li><a href="#{s}">{l}</a></li>'
                       for s, l in cfg["nav"])
    trust = "".join(
        f'<div class="item">{icon(i)}{txt}</div>' for i, txt in cfg["trust"])
    menu = "".join(
        f'<div class="row"><span class="n">{n}<span class="d">{d}</span></span>'
        f'<span class="dots"></span><span class="p">{p}</span></div>'
        for n, d, p in cfg["menu"])
    tiles = "".join(
        f'<div class="tile{" big" if i == 0 else ""}">{txt}</div>'
        for i, txt in enumerate(cfg["gallery"]))
    stats = "".join(
        f'<div class="stat"><div class="n">{n}</div><div class="l">{l}</div></div>'
        for n, l in cfg["stats"])
    quotes = "".join(
        f'<div class="quote reveal"><div class="stars">★★★★★</div>'
        f'<p>“{q}”</p><div class="who">— [ your real review ]</div></div>'
        for q in cfg["quotes"])
    hours = "".join(f"<tr><td>{d}</td><td>{h}</td></tr>" for d, h in cfg["hours"])
    lines = "".join(cfg["contact_lines"])
    about_ps = "".join(f"<p>{p}</p>" for p in cfg["about"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{cfg['title']}</title>
<meta name="description" content="{cfg['desc']}">
<meta property="og:title" content="{cfg['name']} — {cfg['city']}">
<meta property="og:description" content="{cfg['desc']}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600;9..144,700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<script>document.documentElement.classList.add('js')</script>
<style>{css(t)}</style>
</head>
<body>

<header><div class="wrap"><nav>
  <a class="brand" href="#top">{cfg['name']}</a>
  <ul class="navlinks">{navlinks}</ul>
  <a class="nav-cta" href="{cfg['cta_href']}">{cfg['cta_label']}</a>
</nav></div></header>

<main id="top">
<section class="hero"><div class="wrap hero-grid">
  <div class="reveal">
    <span class="eyebrow">{cfg['eyebrow']}</span>
    <h1>{cfg['headline']}</h1>
    <p class="lede">{cfg['lede']}</p>
    <div class="hero-cta">
      <a class="btn btn-primary" href="{cfg['cta_href']}">{cfg['hero_cta']} {icon('arrow')}</a>
      <a class="btn btn-ghost" href="#{cfg['nav'][0][0]}">{cfg['hero_cta2']}</a>
    </div>
    <div class="hero-note">{icon('check', style='width:17px;height:17px;color:var(--accent)')}{cfg['hero_note']}</div>
  </div>
  <div class="hero-art reveal">
    <div class="frame">{cfg['hero_photo']}</div>
    <div class="hero-badge"><div class="num">{cfg['badge_num']}</div>
      <div class="lbl">{cfg['badge_lbl']}</div></div>
  </div>
</div></section>

<div class="trust"><div class="wrap">{trust}</div></div>

<section id="{cfg['nav'][0][0]}"><div class="wrap">
  <div class="sec-head reveal"><span class="eyebrow">{cfg['menu_eyebrow']}</span>
    <h2>{cfg['menu_head']}</h2><p>{cfg['menu_sub']}</p></div>
  <div class="menu reveal">{menu}</div>
  <p class="placeholder-note">Prices shown are placeholders for this preview — you set the real menu and it's updated before launch.</p>
</div></section>

<section id="{cfg['nav'][1][0]}" style="padding-top:0"><div class="wrap">
  <div class="sec-head reveal"><span class="eyebrow">The work</span>
    <h2>{cfg['work_head']}</h2><p>Your real photos go here. Send 4–6 favorites and they're on the site the same day.</p></div>
  <div class="gallery reveal">{tiles}</div>
</div></section>

<section class="about" id="{cfg['nav'][2][0]}"><div class="wrap about-grid">
  <div class="about-art reveal">{cfg['about_photo']}</div>
  <div class="reveal"><span class="eyebrow">About {cfg['short']}</span>
    <h2>{cfg['about_head']}</h2>{about_ps}
    <div class="stats">{stats}</div></div>
</div></section>

<section id="reviews"><div class="wrap">
  <div class="sec-head reveal"><span class="eyebrow">Reviews</span>
    <h2>What clients say.</h2><p>Your real reviews drop in here — these show the layout.</p></div>
  <div class="quotes">{quotes}</div>
</div></section>

<section class="visit" id="{cfg['nav'][3][0]}"><div class="wrap visit-grid">
  <div class="reveal"><span class="eyebrow">Visit</span>
    <h2>{cfg['visit_head']}</h2>
    <p class="sub">{cfg['visit_sub']}</p>
    <div class="lines">{lines}</div>
    {cfg.get('visit_note','')}
  </div>
  <div class="hours reveal"><h3>Hours</h3><table>{hours}</table>
    <p class="fine">Placeholder hours for this preview — confirmed with you before launch.</p></div>
</div></section>
</main>

<footer><div class="wrap">
  <div class="fbrand">{cfg['name']}</div>
  {cfg['footer']}
  <div style="margin-top:14px;color:#6d675e;font-size:.8rem">Site preview crafted for {cfg['name']} by Blankd Web Studio · $300 flat · live in 48 hours</div>
</div></footer>

<script>
  const els=document.querySelectorAll('.reveal');
  if('IntersectionObserver' in window){{
    const io=new IntersectionObserver((es)=>{{es.forEach((e,i)=>{{if(e.isIntersecting){{e.target.style.transitionDelay=(Math.min(i,4)*60)+'ms';e.target.classList.add('in');io.unobserve(e.target)}}}})}},{{threshold:.12,rootMargin:'0px 0px -8% 0px'}});
    els.forEach(el=>io.observe(el));
    setTimeout(()=>els.forEach(el=>el.classList.add('in')),4000);
  }} else {{ els.forEach(el=>el.classList.add('in')); }}
</script>
</body>
</html>
"""


def phone_line(num, href):
    return f'<a class="line" href="tel:{href}">{icon("phone")}{num}</a>'


def mail_line(addr):
    return f'<a class="line" href="mailto:{addr}">{icon("mail")}{addr}</a>'


def addr_line(addr):
    return f'<div class="line">{icon("pin")}{addr}</div>'


NAV_BARBER = [("services", "Services"), ("work", "Work"),
              ("about", "About"), ("visit", "Visit")]

SITES = {
  "icuttz": dict(
    theme="dark", name="Icuttz Barber Lounge", short="Icuttz", city="Bemidji, MN",
    title="Icuttz Barber Lounge — Vintage-Inspired Cuts | Bemidji, MN",
    desc="Icuttz Barber Lounge in downtown Bemidji — vintage-inspired barbershop. Skin fades, beard work and hot-towel straight razor shaves.",
    nav=NAV_BARBER, cta_href="#visit", cta_label="Book online",
    eyebrow="Est. 2024 · Downtown Bemidji",
    headline="Vintage vibe.<br><em>Modern fade.</em>",
    lede="A vintage-inspired barber lounge on Minnesota Ave — sharp skin fades, clean tapers, beard work and hot-towel straight razor shaves.",
    hero_cta="Book your chair", hero_cta2="See services",
    hero_note="Best of the Midwest reader favorite",
    hero_photo="[ your photo — the lounge, or your best fade ]",
    badge_num="4.9", badge_lbl="From 130+<br>reviews",
    trust=[("star", "<b>4.9</b> from 130+ reviews"), ("shield", "<b>Best of the Midwest</b>"),
           ("cal", "Book online in seconds"), ("pin", "Downtown Bemidji")],
    menu_eyebrow="The menu", menu_head="Walk in a regular.<br>Walk out a legend.",
    menu_sub="Every cut finished with the details most shops skip.",
    menu=[("Signature Cut", "consult, cut, style", "$30"),
          ("Skin Fade", "bald fade, blended clean", "$32"),
          ("Cut &amp; Beard", "the full lineup", "$45"),
          ("Beard &amp; Hot Towel", "shaped, lined, conditioned", "$20"),
          ("Straight Razor Shave", "hot towel, close &amp; smooth", "$30"),
          ("Kids' Cut", "12 &amp; under", "$22")],
    work_head="Fresh from the chair.",
    gallery=["[ your best skin fade ]", "[ beard lineup ]", "[ the lounge ]",
             "[ straight razor shave ]", "[ a cut you're proud of ]"],
    about_photo="[ your photo — you behind the chair ]",
    about_head="Bemidji's barber lounge.",
    about=["Icuttz opened downtown in 2024 with one mission: bring back the barbershop experience. The chair, the conversation, the clean finish — with a vintage feel and a modern edge.",
           "Folks around here are already calling it the best cut Bemidji has seen in years. Come find your chair."],
    stats=[("4.9", "Average rating"), ("130+", "Reviews"), ("2024", "Downtown since")],
    quotes=["Best barber Bemidji has seen in years. Takes his time and the fade is always perfect.",
            "The hot towel shave alone is worth it. Feels like an experience, not a quick trim.",
            "Easy to book, great atmosphere, and I've never left unhappy."],
    visit_head="Book your chair.",
    visit_sub="Downtown Bemidji on Minnesota Ave. Booking recommended — walk in if there's a chair open.",
    contact_lines=[phone_line("(218) 214-6701", "2182146701"),
                   mail_line("Icuttz2020@gmail.com"),
                   addr_line("216 Minnesota Ave NW, Bemidji, MN 56601")],
    visit_note='<p style="color:#7d786f;font-size:.82rem;margin-top:22px">Book-online button feeds your existing Booksy page at launch — nothing changes about how you take appointments.</p>',
    hours=[("Tuesday – Friday", "9am – 6pm"), ("Saturday", "9am – 3pm"), ("Sunday – Monday", "Closed")],
    footer='Vintage-Inspired Barbershop · Downtown Bemidji, MN · <a href="tel:2182146701">(218) 214-6701</a>',
  ),

  "larrys": dict(
    theme="light", name="Larry's Barber Shop", short="Larry's", city="Bemidji, MN",
    title="Larry's Barber Shop — Clean Cuts Since 1969 | Bemidji, MN",
    desc="Larry's Barber Shop has kept Bemidji sharp for over 55 years. Classic cuts, fades, beard work and straight razor shaves. Walk-ins welcome.",
    nav=NAV_BARBER, cta_href="tel:2184444480", cta_label="Call the shop",
    eyebrow="Bemidji · Since 1969",
    headline="Clean cuts.<br><em>Over 55 years.</em>",
    lede="A real, no-nonsense walk-in barbershop that's been keeping Bemidji sharp for generations. Same chair your dad trusted — and probably his dad too.",
    hero_cta="Call the shop", hero_cta2="See services",
    hero_note="Walk-ins welcome, no app required",
    hero_photo="[ your photo — the shop, or a classic cut ]",
    badge_num="55+", badge_lbl="Years cutting<br>in Bemidji",
    trust=[("clock", "<b>Since 1969</b>"), ("star", "<b>4.9</b> from 150 reviews"),
           ("check", "Walk-ins welcome"), ("pin", "Chief Plaza, Bemidji Ave")],
    menu_eyebrow="The menu", menu_head="Honest cuts.<br>Honest prices.",
    menu_sub="Nothing fancy. Just done right, every time.",
    menu=[("Men's Haircut", "classic, clean, done right", "$22"),
          ("Fade &amp; Lineup", "tight, blended, sharp", "$25"),
          ("Beard Trim", "shaped and cleaned up", "$14"),
          ("Straight Razor Shave", "hot towel, close shave", "$26"),
          ("Kids' Cut", "12 &amp; under", "$16"),
          ("Senior Cut", "clean and easy", "$18")],
    work_head="From the chair.",
    gallery=["[ a clean fade ]", "[ classic cut ]", "[ the shop ]",
             "[ straight razor ]", "[ beard work ]"],
    about_photo="[ your photo — the barber at work ]",
    about_head="A Bemidji institution.",
    about=["For more than 55 years, Larry's has been the shop Bemidji trusts for an honest cut at an honest price. No apps, no fuss — walk in, take a seat, leave looking sharp.",
           "Some things don't need reinventing. They just need to be done right, every single time."],
    stats=[("1969", "Serving since"), ("4.9", "Average rating"), ("150", "Reviews")],
    quotes=["Best barbers in town. Classic shop, real conversation, and a perfect cut every time.",
            "Been going here for years. Jim is personable and always gets it right.",
            "No-frills, quick, and a clean haircut at a fair price. Exactly what I want."],
    visit_head="Find the shop.",
    visit_sub="Chief Plaza on Bemidji Ave — easy parking, walk right in. Cash or check.",
    contact_lines=[phone_line("(218) 444-4480", "2184444480"),
                   addr_line("1510 Bemidji Ave N, Ste 115, Bemidji, MN 56601")],
    hours=[("Monday – Thursday", "8am – 4pm"), ("Friday", "8am – 12pm"), ("Saturday – Sunday", "Closed")],
    footer='Clean Cuts Since 1969 · Bemidji, MN · <a href="tel:2184444480">(218) 444-4480</a>',
  ),

  "birdys": dict(
    theme="cool", name="Birdy's Detail", short="Birdy's", city="Fargo–Moorhead",
    title="Birdy's Detail — Auto &amp; Boat Detailing | Fargo–Moorhead",
    desc="Birdy's Detail is a female and locally owned auto and boat detailing shop serving the Fargo-Moorhead area. Interior, exterior and watercraft.",
    nav=[("packages", "Packages"), ("work", "Work"), ("about", "About"), ("contact", "Contact")],
    cta_href="sms:7017305691", cta_label="Get a quote",
    eyebrow="Female &amp; locally owned · Fargo–Moorhead",
    headline="Your ride, back to<br><em>brand-new clean.</em>",
    lede="Detailing for cars, trucks, vans and boats across the Fargo-Moorhead area — interior deep cleans, exterior shine and paint protection, done with care.",
    hero_cta="Get my free quote", hero_cta2="See packages",
    hero_note="Locals Love Us winner · 100% would recommend",
    hero_photo="[ your photo — a finished detail ]",
    badge_num="100%", badge_lbl="Would recommend<br>17 reviews",
    trust=[("check", "<b>100% recommend</b>"), ("shield", "<b>Locals Love Us</b> winner"),
           ("boat", "Cars <b>&amp; boats</b>"), ("pin", "Fargo–Moorhead")],
    menu_eyebrow="Packages", menu_head="Flat pricing.<br>No surprises.",
    menu_sub="Trucks, SUVs and boats priced on size. Ask about a package built for you.",
    menu=[("The Refresh", "interior + wash · ~1.5 hrs", "$99"),
          ("The Full Detail", "inside &amp; out · ~3 hrs", "$199"),
          ("Interior Deep Clean", "shampoo, leather, steam", "$149"),
          ("Exterior &amp; Wax", "clay bar, polish, seal", "$139"),
          ("Boat Detail", "hull, oxidation, upholstery", "Quote"),
          ("Fleet &amp; Recurring", "monthly, priced per vehicle", "Quote")],
    work_head="The work speaks.",
    gallery=["[ your best before/after ]", "[ interior detail ]", "[ a boat you did ]",
             "[ exterior shine ]", "[ wheels &amp; trim ]"],
    about_photo="[ your photo — you at work ]",
    about_head="Detailing done with care.",
    about=["Birdy's Detail is female and locally owned, serving Fargo, West Fargo and Moorhead. Cars, trucks, vans — and boats, which most shops around here won't touch.",
           "Every vehicle gets the same standard: leave it looking better than the day it was bought. That's why 100% of clients would recommend us."],
    stats=[("100%", "Would recommend"), ("Local", "Female owned"), ("Boats", "Yes, we do those")],
    quotes=["My truck honestly looked better than when I bought it. Worth every penny.",
            "Had the boat done before lake season. Oxidation gone, finish was unreal.",
            "Professional, thorough, and the interior looks and smells brand new."],
    visit_head="Ready when you are.",
    visit_sub="Text a photo of your vehicle or boat plus your zip — you'll get an exact price and the next open slot.",
    contact_lines=[phone_line("(701) 730-5691", "7017305691"),
                   addr_line("Serving Fargo, West Fargo &amp; Moorhead")],
    hours=[("Monday – Friday", "8am – 6pm"), ("Saturday", "9am – 3pm"), ("Sunday", "Closed")],
    footer='Auto &amp; Boat Detailing · Female &amp; Locally Owned · Fargo–Moorhead · <a href="tel:7017305691">(701) 730-5691</a>',
  ),
}


def main():
    want = sys.argv[1:] or list(SITES)
    os.makedirs(OUT_DIR, exist_ok=True)
    for key in want:
        if key not in SITES:
            print(f"  ?  unknown site '{key}'")
            continue
        path = os.path.join(OUT_DIR, f"{key}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(build(SITES[key]))
        print(f"  built  {key}.html  ({os.path.getsize(path):,} bytes)")


if __name__ == "__main__":
    main()
