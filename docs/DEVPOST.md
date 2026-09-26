# Devpost write-up (draft) — replace NAME everywhere

## Tagline
Grocery forecasts that act on themselves — and prove whether they were right.

## Inspiration
Fresh departments order on gut feel or "same as last week", and the surplus becomes waste. AI
forecasting vendors sell predictions, but a store can't see whether yesterday's prediction was right,
or whether this week's "improved" model is actually better. We wanted a forecaster that earns trust
the way a good employee does: by showing its track record.

## What it does
- **Plans tomorrow** for every fresh product: a P50/P80 demand forecast, an order quantity from a
  shelf-life-aware newsvendor (net of stock still on the shelf), waste-risk flags and markdown
  suggestions, with a slider for the manager's cost of waste vs. cost of a stockout.
- **Logs every prediction before reality arrives** in an append-only ledger, then attaches what
  actually sold. Error (WAPE, bias) is tracked by store, category, weekday and model version.
- **Learns under governance.** Every week with enough new data it trains a challenger on a new
  immutable snapshot. The challenger is promoted only if it beats the champion by ≥2% on days
  neither model has seen, doesn't lose in either week of that window, doesn't make any major
  category >5% worse, beats two naive baselines and passes an overfit guard. Otherwise it's
  rejected, with the reason on screen. Any previous champion can be restored.
- **Explains itself.** A morning briefing (Gemini, read aloud by ElevenLabs) is written only from the
  computed plan. Manager notes ("street festival Saturday") live in Backboard memory and inform the
  briefing without ever changing forecast numbers.

## How we built it
- **Data:** real Rohlik e-grocer sales (Kaggle, 7 warehouses, perishables only, 2020–2024) with
  prices, seven discount types, availability, and each warehouse's real daily order count as a
  customer-traffic signal.
- **Weather done right:** Open-Meteo archived forecasts for training, and for the 2024 replay the
  forecast *actually issued the day before* (Previous Runs API) — no leakage from observed weather.
  The dataset's own weather is kept side by side as a cross-check.
- **Models:** two-stage XGBoost — stage 1 forecasts store traffic (out-of-fold, time-respecting),
  stage 2 forecasts product demand (Tweedie) plus a P80 quantile model. One feature function is
  shared by training and serving; a feature-schema version guards against serving a model on the
  wrong features.
- **Stack:** Python/FastAPI, TimescaleDB (Tiger Data) for the ledger and continuous aggregates of
  forecast error, MongoDB Atlas for model cards, React + TypeScript + Recharts dashboard, one Docker
  image on Vultr behind a .tech domain.

## Results (all on data the model never trained on)
- **14.3% weighted error (WAPE)** over the Jan–Jun 2024 replay vs **24.2%** for "same weekday last
  week" — about 41% less error. (Accuracy, if quoted, = 1 − WAPE ≈ 86% by volume.)
- 10 challengers trained in the replay: 6 promoted, 4 rejected by the promotion rules.
- Holiday-proximity features cut pre-Easter error from 22.0% to 18.3%; seven discount types cut
  replay error from 14.7% to 14.3%.
- Measured and **rejected**: dropping stockout days as targets, a produce specialist model,
  per-store calibration, spike weighting. Weather didn't help this online grocer one day ahead.
- Simulated waste vs. naive ordering: see `artifacts/production_report.json` (FIFO shelf-life
  simulator — labeled simulated; no public dataset records waste).

## Challenges
- Our first replay rejected every challenger. The cause: early stopping held out the newest seven
  weeks, so new data was never learned from. Refitting on the full snapshot fixed it.
- Easter: a 0/1 holiday flag can't say "Easter is in two days"; proximity features could.

## What we'd do next
Connect a real store's POS and door-counter feed, replace the markdown heuristic with the
store's learned response from logged promotion experiments, and add a waste-based promotion metric.

## Honesty notes
Waste and inventory are simulated. Rohlik is an online grocer, so warehouses stand in for stores.
The replay is a backtest on historical data, not live customers. AI tools used: Gemini (briefing
text), ElevenLabs (voice), Cursor (code review), Claude (development assistance); the forecasting,
governance and data pipeline are ours.

## Built with
python, fastapi, xgboost, pandas, timescaledb, tiger-data, postgresql, mongodb-atlas, react,
typescript, recharts, open-meteo, gemini, elevenlabs, backboard, vultr, docker, .tech
