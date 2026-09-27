# Demo script (2.5 minutes)

**Setup before judges arrive:** Dashboard open on **Atlanta**; Today's plan, Upload and Ledger one click
away; a daily sheet ready to drag in (`samples/georgia/`).

## 0:00 — The problem (15 s) · track: A Marina's Mission
"About a third of food is lost or wasted, and fresh food a store throws out takes everything used to
grow, move and chill it to the landfill. Most stores still order on gut feel or 'same as last week'.
Freshora forecasts tomorrow, says what to order, and tells you what to discount or donate before it spoils."

## 0:15 — Dashboard (30 s)
- Switch **Atlanta → Macon → Augusta**: same chain, different stores. Four numbers: customers per day,
  expected sales, what to order (net of what's already on the shelf), units at risk of waste.
- Expected sales by category (bakery, produce, meat, dairy, eggs), and **forecast vs. what sold** for the
  last two weeks: every forecast there was made the day before the sales came in.
- "What to order" + **Download order report**.

## 0:45 — Today's plan (45 s)
- The **morning briefing**: four bullets written from the plan (click one: it opens the Ledger).
- The **order more / order less** slider: order quantities move live — the owner chooses the trade-off.
- A product at risk: the discount that keeps the most money, or "no discount pays — donate".
- Weather for the store's own city, in °F.

## 1:30 — Ask about your store (25 s)
Open the chat bubble (bottom right): "Do I need any promotions today, and what's the lowest discount
where everything still sells?" — the answer comes only from this store's data; every number in it is
checked against that data. Then ask something it can't know ("will it rain Saturday?") — it says so.

## 1:55 — It learns (15 s)
Upload tab: drag in a daily sheet. It's validated row by row, yesterday's forecasts are graded against
what sold, and tomorrow's plan is rebuilt. With enough new days it retrains itself — and a new model is
only used if it beats the current one on days neither has seen and is no worse for the other stores.

## 2:10 — Numbers (15 s)
About page: **13.7% error (86.3% accurate) vs 22.5% for "same as last week"** on four weeks of real sales the model never
saw (39% less error). Waste is simulated (no public dataset records it) and labeled.

## 2:25 — Close (5 s)
"Order right, discount only when it pays, donate the rest — and never trust a model that hasn't proven
itself on days it has never seen."

## Likely judge questions
| Question | Answer |
|---|---|
| Isn't this Afresh / Wasteless? | They sell forecasts and markdowns; the governed, visible prediction→outcome loop is internal at those companies. Ours is the product. |
| Is weather leaking? | No. Training uses archived forecasts; the 2024 replay uses forecasts issued the day before (Open-Meteo Previous Runs). Dataset weather is kept only as a cross-check. |
| Where does waste come from? | A FIFO shelf-life simulator (produce 3 days, bakery 2, meat 4, dairy 10, eggs 21) — labeled simulated. |
| Are Atlanta, Macon and Augusta real stores? | No — a demo chain. Each replays one of the three largest real grocery warehouses (Rohlik) at a Georgia location — its history was onboarded into training, and the four live weeks were never trained on, through the same upload path a real store uses. Dairy and eggs are synthetic (the dataset has none) and are left out of the headline accuracy. |
| Why only one database? | Everything is relational and time-based: forecasts joined to outcomes, promotions as transactions, error over time as a Timescale continuous aggregate. Model cards are assembled from the same database, so there's one source of truth. |
| What did you try that didn't work? | Censoring stockout days, a produce specialist, per-store calibration, spike weighting — all measured, all rejected by the same promotion bar (docs/ARCHITECTURE.md §4.5). |
| Is it overfitting? | Train WAPE ~12% vs ~14% on unseen data; an overfit guard rejects challengers whose eval/train ratio exceeds 1.5. |
