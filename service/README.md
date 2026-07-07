# Blankd Web Studio — Operations

The $300-flat, 48-hour website service. This folder is the whole storefront.

## Deploy the landing page (free, ~10 minutes)

**Option A — Netlify Drop (easiest):**
1. Go to https://app.netlify.com/drop (free account)
2. Drag the whole `service/` folder in
3. You get a live URL immediately (e.g. `blankd.netlify.app`). Rename the site in
   settings to something clean.

**Option B — GitHub Pages:**
1. Repo → Settings → Pages → deploy from branch, root or `/service`
2. Live at `yourusername.github.io/proj-1/service/site/`

**Domain (recommended, ~$12/yr):** buy on Porkbun or Cloudflare (avoid GoDaddy
renewal pricing). Point it at Netlify/Pages — both have one-click custom domain
setup. A real domain roughly doubles perceived legitimacy for cold leads.

> The demo links on the landing page (`../demos/barber.html`) work as long as you
> deploy the whole `service/` folder together.

## Getting paid

- **Stripe Payment Links** (no code, free account): create two links — "$150 deposit"
  and "$150 final". Looks professional, takes cards.
- Venmo/Zelle/Cash App are fine too — local businesses often prefer them.
- Never start a build without the deposit. Ever. This is the only hard rule.

## The build loop (how you and I work a client)

1. You close the deal + collect the deposit
2. You paste me everything: business name, niche, services + prices, hours,
   address, phone, photos/logo, and what the main action is (call / book / visit)
3. I build the full site in this repo under `service/clients/<business-name>/`
4. You send them the preview link, collect feedback, paste it to me
5. I revise (2 rounds included), you deliver, collect final payment
6. Offer the $10/mo care plan — edits also flow through me, so your marginal
   effort is forwarding a text message

## Custom mockups (the secret weapon)

For any warm lead, ask me for a **free personalized mockup** — I'll build a real
homepage with their actual business name, niche and city. You screenshot it and
send it. "Here's YOUR site" converts wildly better than "here's my portfolio."
Cost to us: nothing. That's the unfair advantage of this partnership.
