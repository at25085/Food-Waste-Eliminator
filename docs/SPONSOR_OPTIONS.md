# Sponsor challenge options (decide in the morning)

Main track: **A Marina's Mission** (Social Good). Sponsor challenges stack on top. Both options
below build on what already exists: tomorrow's plan flags last-day stock that a markdown won't
clear (`surplus_action`, `donate_units` in `/api/stores/{id}/recommendations`).

## Visa — "Reimagine Shopping" ($5,000, 1 winner) → **"Last Call"**

**Pitch:** your shopping agent knows what you actually buy; when a store has fresh food it's about
to throw away, the agent reserves a rescue bundle for you at a markdown, pays within the limit you
set, and you pick it up. Shoppers save money, stores sell instead of binning, the planet wins.

**Why it fits Visa:** agentic commerce (Visa Intelligent Commerce / Trusted Agent Protocol exist
for exactly this: an agent paying on a user's behalf within spend controls), personalization,
generative AI (bundle + recipe suggestion), trust (spend limit, one-tap consent).

**Build (≈4–6 h):**
1. Offers feed: `GET /api/offers?store=` — items with `surplus_action` ∈ {markdown, markdown_then_donate},
   discounted price, pickup window. (No keys needed — can be built tonight/tomorrow morning.)
2. Shopper profile: a few past purchases (demo persona) → Gemini ranks offers and writes a bundle +
   a recipe that uses them ("rescue dinner for two, $6.40").
3. Checkout: Visa Intelligent Commerce sandbox (needs Visa developer credentials — ask the Visa booth
   whether they provide keys). Fallback: a clearly labeled simulated checkout with the same flow.
4. Loop closure: each purchase is a promotion-experiment record → the store learns real markdown
   response (already an API).

**Honest risks:** Flashfood / Too Good To Go already sell surplus to shoppers — our angle is the
*agent* that buys for you from a forecast, not a deals app. Without Visa credentials the payment is
simulated; say so.

## Meta — "Bringing People Closer Together with AI" (3 winners → Menlo Park) → **"Community Table"**

Rubric (per the brief): human connection 30%, AI essential 25%, originality 20%, execution 20%.

**Pitch:** the food a store can't sell becomes a reason for neighbors to meet: surplus is matched to
community fridges and volunteer groups, the AI coordinates who picks up what, and suggests a shared
meal the pickup can become (e.g., a building's Sunday potluck built around 12 loaves and 8 kg of
produce).

**Build (≈4–6 h):** donation feed (exists) → a small "community partners" list per store → Gemini
matches surplus to partners and drafts pickup coordination messages → a volunteer view where two
volunteers claim a pickup together → a shared-meal suggestion card.

**Honest assessment:** weakest fit of the three. Meta weights *human connection* at 30%, and this
reads as logistics unless the people part is visibly the product. Only worth it if the demo centers
on the neighbors, not the store. Recommendation: do Visa first; Meta only if time remains.

## Not doing
NSA (Hearsay / Packet Pursuit), Impiricus — per team decision.
