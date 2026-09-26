# Demo script (2.5 minutes) — replace NAME with the final product name

**Setup before judges arrive:** dashboard open on *Today* (store Prague_1), *Learning loop* tab
loaded, replay speed set to fast, audio on if ElevenLabs is configured. Have the Model Health tab
one click away.

## 0:00 — The problem (15 s)
"Grocers throw away fresh food every day because they order on gut feel or 'same as last week'.
Forecasting startups sell predictions — but a prediction you can't audit is just a guess with a
dashboard. NAME forecasts tomorrow, tells the manager what to order and mark down, and proves
whether it was right."

## 0:15 — Today (40 s)
- Point at the plan: tomorrow's forecast per product, order quantity, waste-risk badges, markdowns.
- Drag the **waste vs. stockout slider** → order quantities move live. "The manager chooses the
  trade-off; the math is a newsvendor on our P50/P80 forecasts, net of stock already on the shelf."
- Play the **morning briefing** (Gemini writes it from the computed plan; it's never allowed to add
  numbers). Add a manager note — "street festival Saturday" — and regenerate: the briefing mentions
  it, the forecast numbers don't change. "Notes explain; they never secretly move forecasts."

## 0:55 — The learning loop (60 s) — the centerpiece
"This is a backtest: January to June 2024 on real Rohlik grocery data, using the weather forecast
that was actually issued the day before."
- Press play. Every day the champion's predictions go to an append-only ledger; the next day's
  sales are attached; error ticks.
- A challenger is trained every week there is enough new data. **It is promoted only if it beats
  the champion by 2% on days neither model has seen, loses in neither week, and no category gets
  more than 5% worse.** Show a promotion (version badge flips) and a **rejection with its reason** —
  "newer isn't automatically better."
- Easter: "Monitoring caught a pre-holiday under-forecast. We added holiday-proximity features; the
  new version had to earn promotion — pre-Easter error went from 22% to 18%."

## 1:55 — Honesty & numbers (25 s)
- Model Health: **14.3% weighted error vs 24.2% for same-weekday-last-week** (about 41% less error),
  P80 coverage 80%. Weather didn't help this online grocer one day ahead — we measured it and say so.
- "Waste is simulated — no public dataset records it — and it's labeled everywhere."
- Rollback: any previously promoted version can be restored; nothing is ever overwritten.

## 2:20 — Close (10 s)
"Start useful with history, become store-specific by watching what actually happens — and never
promote a model that hasn't proven itself on data it has never seen."

## Likely judge questions
| Question | Answer |
|---|---|
| Isn't this Afresh / Wasteless? | They sell forecasts and markdowns; the governed, visible prediction→outcome loop is internal at those companies. Ours is the product. |
| Is weather leaking? | No. Training uses archived forecasts; the 2024 replay uses forecasts issued the day before (Open-Meteo Previous Runs). Dataset weather is kept only as a cross-check. |
| Where does waste come from? | A FIFO shelf-life simulator (produce 3 days, bakery 2, meat 4) — labeled simulated. |
| Why two databases? | Time series (ledger, error aggregates) in TimescaleDB; model cards (nested, schema-changing documents) in MongoDB Atlas. Postgres JSONB could hold cards too; we used the store built for each shape. |
| What did you try that didn't work? | Censoring stockout days, a produce specialist, per-store calibration, spike weighting — all measured, all rejected by the same promotion bar (docs/ARCHITECTURE.md §4.5). |
| Is it overfitting? | Train WAPE ~12% vs ~14% on unseen data; an overfit guard rejects challengers whose eval/train ratio exceeds 1.5. |
