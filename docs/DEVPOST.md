# Devpost write-up (draft) — replace NAME everywhere

**Main track: A Marina's Mission (Social Good).** Sponsor / MLH: Tiger Data, MongoDB Atlas,
Gemini, ElevenLabs, Backboard, Vultr, .Tech, Notability, SpaceXAI (Cursor) — plus Visa if "Last Call"
ships (see docs/SPONSOR_OPTIONS.md).

## Tagline
Keep fresh food out of the bin: forecasts that act on themselves — and prove they were right.

## Inspiration
Roughly a third of the food supply goes unsold or uneaten, and wasted food carries its whole
life-cycle footprint to the landfill. Fresh departments still order on gut feel or "same as last
week". Forecasting vendors sell predictions, but a store can't see whether yesterday's prediction
was right — or whether this week's "improved" model actually is. We wanted a forecaster that
prevents waste first, earns trust by showing its track record, and routes what can't be sold to
people instead of the bin.

## What it does
- **Prevents waste at the source.** Tomorrow's P50/P80 forecast per fresh product drives a
  shelf-life-aware order quantity, net of stock already on the shelf. A slider sets how much the
  store fears waste vs. empty shelves.
- **Follows the EPA Wasted Food Scale for what's left:** prevent → mark down → donate. Last-day
  stock a markdown won't clear is flagged for a food-recovery partner, with meals and CO2e shown.
- **Logs every prediction before reality arrives** (append-only ledger) and attaches what actually
  sold; error and bias are tracked by store, category, weekday and model version.
- **Learns under governance.** A challenger is promoted only if it beats the champion by ≥2% on days
  neither has seen, doesn't lose in either week of that window, doesn't make any major category >5%
  worse, beats two naive baselines and passes an overfit guard — otherwise it's rejected with the
  reason on screen. Any previous champion can be restored.
- **Explains itself.** A morning briefing (Gemini, read aloud by ElevenLabs) written only from the
  computed plan; manager notes live in Backboard memory and inform the briefing without ever
  changing forecast numbers.

## Results (all on data the model never trained on)
- **Forecast error 14.3% (WAPE)** over a Jan–Jun 2024 replay vs **24.2%** for "same weekday last
  week" — about 41% less error.
- **Simulated waste vs. naive ordering at a realistic service level (≈5% lost sales):** ≈87% less
  waste *and* ≈26% fewer lost sales — waste isn't cut by simply ordering less. (FIFO shelf-life
  simulator; no public dataset records waste. See the Impact screen for kg, CO2e and meals, with
  cited factors and labeled assumptions.)
- Replay: 10 challengers trained, 6 promoted, 4 rejected by the promotion rules.
- Holiday-proximity features cut pre-Easter error from 22.0% to 18.3%; seven discount types cut
  replay error 14.7% → 14.3%.
- Measured and **rejected**: dropping stockout days as targets, a produce specialist model,
  per-store calibration, spike weighting. Weather didn't help this online grocer one day ahead.

## How we built it
- **Data:** real Rohlik e-grocer sales (Kaggle; 7 warehouses; perishables only; 2020–2024) with
  prices, seven discount types, availability, and each warehouse's real daily order count as a
  customer-traffic signal.
- **Weather done right:** Open-Meteo archived forecasts for training and, for the 2024 replay, the
  forecast actually issued the day before (Previous Runs API) — no leakage from observed weather.
  The dataset's own weather is kept side by side as a cross-check.
- **Models:** two-stage XGBoost — stage 1 forecasts store traffic (out-of-fold, time-respecting),
  stage 2 forecasts product demand (Tweedie) plus a P80 quantile model; one feature function shared
  by training and serving, guarded by a feature-schema version.
- **Stack:** Python/FastAPI; TimescaleDB (Tiger Data) hypertables + a continuous aggregate of
  forecast error; MongoDB Atlas model cards; React + TypeScript + Recharts; one Docker image on
  Vultr behind a .tech domain.

## Impact factors (cited)
- 2.67 kg CO2e per kg of wasted food — WRAP UK 2021-22 (≈16 Mt CO2e / 6.0 Mt).
- ≈0.55 kg (1.22 lb) of food per meal — derived from ReFED (29% of 240 M tons ≈ 114 B meals).
- Unit weights per category are **assumptions** (Rohlik records pieces or kg by product).

## Challenges
- Our first replay rejected every challenger: early stopping held out the newest seven weeks, so
  new data was never learned from. Refitting on the full snapshot fixed it.
- Easter: a 0/1 holiday flag can't say "Easter is in two days"; proximity features could.
- Our first waste numbers were flattering because the default ran the store at 13% stockouts; we
  moved to a realistic service level and report waste and lost sales together.

## What's next
Connect a real store's POS and door counter, real food-recovery partners, and replace the markdown
heuristic with the store's learned response from logged promotion experiments.

## Honesty notes
Waste, inventory and impact are simulated/derived and labeled. Rohlik is an online grocer, so
warehouses stand in for stores. The replay is a backtest, not live customers. AI tools: Gemini
(briefing text), ElevenLabs (voice), Cursor (code review), Claude (development assistance); the
forecasting, governance and data pipeline are ours.

## Built with
python, fastapi, xgboost, pandas, timescaledb, tiger-data, postgresql, mongodb-atlas, react,
typescript, recharts, open-meteo, gemini, elevenlabs, backboard, vultr, docker, .tech
