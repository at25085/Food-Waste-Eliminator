# WasteLess — Architecture (working name)

> **Name warning.** "Wasteless" is a live, funded company (wasteless.com) that sells AI markdowns for
> perishables to grocers. Rename before Devpost submission. The code package is named `forecaster`
> and the display name is one config value (`APP_DISPLAY_NAME`), so a rename is a one-line change.

## 1. What the system is

A perishable-demand forecasting platform for grocers that **acts** on its predictions (order and
markdown recommendations, waste-risk alerts) and **learns** whether those predictions and actions
were right, through a governed champion/challenger loop.

Two feedback loops:

| Loop | Steps |
|---|---|
| Operational | predict demand → detect waste risk → recommend order / markdown → sell-through |
| Learning | log prediction → attach observed outcome → measure error → accumulate first-party data → train challenger → temporal validation → promote only if better |

Philosophy: *start useful with available historical data; become store-specific as the platform
observes how the store actually operates.*

## 2. Data sources (verified 2026-09-25)

### 2.1 Bootstrap dataset — Rohlik (real, not synthetic)

Rohlik is a Czech online grocer. Its public Kaggle competition data is mirrored as Parquet in
`autogluon/fev_datasets` on HuggingFace (no Kaggle login needed; `scripts/download_data.py`).

| File | Grain | Content |
|---|---|---|
| `rohlik_sales_1D` | product × warehouse × day | 5,390 series; **perishables only** (Fruit & vegetable 3,246 · Bakery 1,611 · Meat & fish 533); `sales`, `sell_price_main`, `availability`, 7 discount types, holiday / school-holiday / shops-closed flags, **`total_orders`**; 2020-08-01 → 2024-06-02 |
| `rohlik_orders_1D` | warehouse × day | 7 warehouses; `orders`, holidays, shutdowns, **`precipitation`, `snow`** (dataset weather), user activity; 2020-12-05 → 2024-03-15 |

"Store" = Rohlik warehouse (Prague_1/2/3, Brno_1, Budapest_1, Munich_1, Frankfurt_1).
**`total_orders` is a real, first-party customer-count signal** (orders ≈ customer visits for an
e-grocer). We do not fabricate traffic for any store: stores without a traffic feed show
*"Customer traffic not connected"*.

Honest limitations (shown in the UI and the Devpost write-up):
- Rohlik is an online grocer, so its warehouses stand in for stores.
- No public dataset records waste or expiry. Waste is produced by a transparent **inventory
  simulator** and always labeled *simulated*.
- `availability < 1` means sales are **censored** (stock-outs); we carry it as a feature and a flag.

### 2.2 Open-Meteo — two distinct uses

| Use | API | Coverage | Role |
|---|---|---|---|
| Training enrichment | Historical Forecast API `historical-forecast-api.open-meteo.com/v1/forecast` | 2021-22 → now (ECMWF IFS from 2017) | Archived forecast series in the same format as the live API. Stitched from the first hours of each run, so it behaves like a ~D+0/D+1 forecast. |
| Lead-time-true training (2024) | Previous Runs API `previous-runs-api.open-meteo.com/v1/forecast` (`*_previous_day1`) | Jan 2024 → now | What was actually forecast one day before. Used for the 2024 holdout / replay. |
| Cross-check only | Historical Weather (ERA5) `archive-api.open-meteo.com/v1/archive` | 1940 → now | Observed-like weather to cross-check the dataset's own weather. Never a model feature. |
| Live serving | Forecast API `api.open-meteo.com/v1/forecast` | now → +16 days | Tomorrow's forecast for every live prediction. |

Rules:
- **Keep both.** Dataset columns are renamed `dataset_precipitation`, `dataset_snow`; API columns
  are `api_*`. A cross-check report compares them. Nothing is silently overwritten.
- **Dataset weather is never a model feature**: it is the observed weather of the day, which does
  not exist at prediction time (that would be target-time leakage and training/serving skew).
- **One normalized schema** regardless of origin: `weather_temperature_max`,
  `weather_temperature_min`, `weather_precipitation_sum`, `weather_code`,
  `weather_wind_speed_max`, plus `weather_source` ∈ {`historical_forecast`, `previous_runs_d1`,
  `live_forecast`} kept as metadata (not a feature).
- Every API response is cached on disk (`data/cache/open_meteo/…json`) keyed by
  (api, lat, lon, date range, variables). Bulk-fetch one call per city per range.
- Free tier is non-commercial, CC-BY 4.0: attribution in the UI footer.

## 3. Feature pipeline (single code path for training and serving)

`forecaster.features.build_features(frame, as_of)` is the only function that produces model
inputs. Training and serving both call it; serving passes rows whose target is unknown.

Prediction semantics: a prediction is created at the end of day *t* (after *t*'s sales are known)
for `forecast_date = t + h`, `h ≥ 1`. All lags are computed relative to `t`, never to the target
date, so no feature can see the future.

| Group | Features |
|---|---|
| Calendar (known in advance) | dow, month, day_of_year, week_of_year, is_weekend, holiday, school_holidays, winter_school_holidays, shops_closed; holiday proximity from the public calendar (`holidays` package ∪ dataset flags): days_to_next_holiday, days_since_last_holiday, next/last holiday type (Easter, Christmas, other), days_to_next_closure |
| Product | category L1/L2/L3 (categorical codes), product_id code, warehouse code |
| Demand history | sales_lag_{h..h+13} subset (1,2,3,7,14), rolling mean/std 7/28, same_weekday_mean_4w, ewm |
| Price / promo (planned, known ahead) | sell_price_main, price_rel_28d, discount_max, any_discount, n_discount_types, the seven discount types (type_0…type_6), discount_max 7 days earlier |
| Availability | availability_lag_1, stockout_rate_28d (target-day availability is **not** a feature) |
| Weather (normalized) | weather_temperature_max/min, weather_precipitation_sum, weather_code group, weather_wind_speed_max, temp anomaly vs 28d |
| Traffic (only when a store has a traffic feed) | customer_count_lag_1/7/14, customer_count_rolling_7/28_mean, same_weekday_customer_mean, units_per_100_customers_28d, category_units_per_100_customers_28d, **expected_customer_count** (stage-1 forecast) |

Missing traffic is represented as NaN (XGBoost handles missing natively), so one model serves
stores with and without traffic.

`FEATURE_SCHEMA_VERSION` is bumped whenever this list changes; every model records the schema
version it was trained on, and serving refuses to use a model whose schema differs.

## 4. Models

### 4.1 Stage 1 — customer traffic
`expected_customer_count` per store-day from weekday, month, holidays, weather forecast, lagged
traffic. XGBoost regressor. Training rows for stage 2 use **out-of-fold, time-respecting**
stage-1 predictions (expanding-window cross-fitting), so stage 2 never trains on a traffic value
it could not have had.

### 4.2 Stage 2 — product demand
XGBoost (`reg:tweedie`, histogram trees, native categorical), one global model across products and
stores (Level 1–2 personalization: global + store identity). Target: daily sales.
Also trained: P50 point and a P80 quantile model (`reg:quantileerror`) for order sizing.

### 4.3 Baselines (always reported)
seasonal-naive (lag 7) and 28-day rolling mean. A model that does not beat both is not promoted.

### 4.4 Personalization roadmap (tested, never assumed)
L1 global → L2 global + store features → L3 global + per-store calibration (isotonic / bias
offset learned on the store's own ledger) → L4 per-store model for stores with enough history.
Same for category specialists (produce / bakery / meat). Each level is a challenger and must win.

### 4.5 Measured changes (fixed harness: train through 2024-01-19, score 2024-01-20 → 2024-06-02)

Kept only if WAPE drops ≥ 0.003 with no major category > 5% worse; results in
`artifacts/accuracy_experiments.json`.

| Change | WAPE | Decision |
|---|---|---|
| fs_v3 → fs_v4 holiday proximity (replay) | 14.95% → 14.69%; pre-Easter 22.0% → 19.2% | kept |
| Seven discount types + lagged discount (fs_v5) | 15.70% → 15.24% | kept |
| Drop stockout days as targets | 15.53% (worse) | rejected |
| Produce specialist model | 15.29%, produce worse | rejected |
| Per-store calibration (L3) | 15.15%; bias −4.2% → −1.7% | rejected on WAPE bar — revisit with a waste-based promotion metric |
| Volatility sample weights | 15.11% | rejected |
| Weather features (ablation) | no gain at D+1 for this online grocer | kept in pipeline, reported honestly |

## 5. Decisions (the operational loop)

- **Order quantity**: shelf-life-aware newsvendor. Critical ratio `cu / (cu + co)` from
  stockout cost vs. waste cost (configurable, slider in UI) → pick the forecast quantile, **net of
  units still sellable tomorrow** (FIFO: last-day units sell first). The API and the waste simulator
  use the same rule, and model vs. baseline are simulated with the same ordering function, so the
  comparison measures forecast skill only.
- **Waste risk**: projected on-hand (simulated inventory) over remaining shelf life minus the
  cumulative forecast; risk if positive.
- **Markdown suggestion**: hackathon heuristic (depth by days-to-expiry and excess ratio), with a
  model-based lift estimate (predict with and without the discount). Markdowns only lower
  prices; never surge pricing.
- **Promotion experiment record** for every markdown: product, discount, window, inventory
  before, forecast_without_promotion, actual sales, inventory after, waste after, revenue. This
  accumulates the data that later replaces the heuristic with learned, store-specific elasticity.

Shelf lives (simulator defaults, configurable): Fruit & vegetable 3 d, Bakery 2 d, Meat & fish 4 d.

## 6. The learning loop

### 6.1 Storage (PostgreSQL + TimescaleDB; SQLite for tests)

| Table | Mutability | Purpose |
|---|---|---|
| `stores` | mutable | store id, city, lat/lon, timezone, traffic_connected |
| `observations` | append-only | validated daily facts per product-store-day (units sold, price, availability, inventory, waste) — hypertable on `date` |
| `traffic_observations` | append-only | timestamp, store_id, customers_entered, transactions, units_sold, revenue — hypertable |
| `quarantine` | append-only | rejected / suspicious rows with the failed check |
| `anomaly_days` | mutable | store_id, date, is_anomaly, reason, training_policy ∈ {exclude, feature, keep} |
| `model_versions` | **immutable** rows (status column may change) | version, stage, training_data_range, feature_schema_version, params, metrics, artifact_uri, created_at, promotion_status ∈ {candidate, champion, rejected, retired}, decision_reason |
| `champion_history` | append-only | who was champion when; rollback target |
| `predictions` | **append-only, never updated** | prediction_id, store, product, prediction_created_at, forecast_date, horizon, predicted_units, p80_units, weather snapshot (JSON), expected_customers, model_version, feature_schema_version — hypertable |
| `outcomes` | append-only | prediction_id FK, actual_units_sold, actual_customer_count, actual_inventory_remaining, waste_units, observed_at |
| `promotion_experiments` | append-only | the record in §5 |
| `retrain_runs` | append-only | trigger, data snapshot, candidate version, per-segment comparison, decision |

Predictions and outcomes are separate tables joined by `prediction_id`, so what the system
believed *before* reality is never overwritten. Timescale continuous aggregates compute daily
error rollups per model version / store / category.

### 6.2 Monitoring
Error = predicted − actual (positive = over-forecast, which creates waste).
Metrics: MAE, RMSE, WAPE = Σ|e| / Σactual, bias = Σe / Σactual.
Views: last 7 / 30 days × {global, product, category, store, weekday}.
Drift signal: 30-day WAPE > (reference WAPE × 1.15) for 7 consecutive days → retraining
*evaluation* (never automatic promotion). Persistent bias beyond ±5% raises a waste warning.

### 6.3 Data-quality validation (before anything enters training data)
Negative sales, negative customer counts, duplicates, missing timestamps, impossible prices
(≤0 or >10× rolling median), extreme inventory jumps, malformed ids. Failing rows go to
`quarantine`; nothing is silently trained on or silently dropped. Unusual-but-valid days are
flagged in `anomaly_days`, not deleted.

### 6.4 Controlled retraining
```
new validated data → min threshold reached (default 14 new days; configurable per store volume)
  → snapshot → train challenger (new immutable version)
  → evaluate on the 14 days after the snapshot (unseen by the challenger and the champion)
  → compare vs champion on identical rows
  → promote iff  global WAPE improves ≥ 2%  AND  no major category degrades > 5%
                AND the challenger does not lose in either 7-day half of the window
                AND beats both baselines AND train/validation gap below overfit guard
  → else reject (recorded with reason)
```
A full 3-fold rolling-origin evaluation (a separate challenger trained per fold) would be stronger,
but triples retraining cost; it is on the roadmap, and the doc claims only what is implemented.
Evaluated weekly; trained only when the threshold is met. A model never mutates: snapshot A →
v1, snapshot B → v2.

Training a candidate: early stopping on the last 7 weeks of the snapshot picks the tree count,
then the candidate is **refit on the whole snapshot** with that count scaled for the extra data.
Without the refit, the newest weeks — exactly the new first-party data — would only ever be used
for validation and never learned from (a bug we caught in the first replay: every challenger lost).
The evaluation window stays strictly after the snapshot, so the comparison is still on unseen data,
and the artifact that was evaluated is exactly the artifact that is promoted. Rollback re-points the champion to any previous version and writes a
`champion_history` row. Overfitting guard: warn if validation WAPE / train WAPE > 1.5.

### 6.5 Honest hackathon demonstration
- **Model Health panel**: current champion, training range + weather source, measured test WAPE,
  new real store observations `0 / threshold`, next retraining eligibility.
- **Historical replay (labeled "backtest", never "live customers")**: replay Jan–Jun 2024 day by
  day using Previous Runs D+1 forecasts. Each simulated day the champion writes predictions,
  the next day's actuals are attached as outcomes, metrics tick, and every 14 days a challenger
  is trained and either promoted or rejected — both outcomes are real results of the replay.
- **Data Sources panel**: ✅ sales history ✅ inventory (simulated) ✅ prices ✅ Open-Meteo
  ✅/⬜ customer traffic (per store) ⬜ local events (future).

## 7. Services and API

```
backend/forecaster/
  config.py          settings (env), promotion thresholds, shelf lives, display name
  data/              download, load Rohlik, validation, anomaly flags
  weather/           Open-Meteo client + disk cache + normalization + cross-check
  features/          build_features (single path), schema version
  models/            stage1 traffic, stage2 demand, baselines, metrics, registry
  pipeline/          train_bootstrap, retrain (champion/challenger), replay
  decisions/         newsvendor, waste risk, markdown heuristic, inventory simulator
  db/                SQLAlchemy Core schema + repositories (Postgres/Timescale, SQLite)
  api/               FastAPI app
```

| Endpoint | Purpose |
|---|---|
| `GET /api/stores` | stores + traffic connection state |
| `GET /api/stores/{id}/recommendations?date=` | tomorrow's forecast, order qty, waste risk, markdowns (writes predictions to the ledger) |
| `GET /api/stores/{id}/weather` | live Open-Meteo forecast used for serving |
| `GET /api/model-health` | champion, metrics, thresholds, eligibility |
| `GET /api/models` · `POST /api/models/{v}/rollback` | registry, rollback |
| `GET /api/metrics?window=&by=` | MAE/RMSE/WAPE/bias by segment |
| `GET /api/ledger?store=&product=` | predictions joined with outcomes |
| `POST /api/outcomes` · `POST /api/traffic` (JSON or CSV) | first-party data ingestion with validation |
| `GET /api/data-sources` | connected signals per store |
| `GET /api/replay` | precomputed backtest timeline (version changes, WAPE, decisions) |

## 8. Frontend
React + Vite + TypeScript + Recharts. Screens: **Today** (per-store recommendations, weather card,
waste-cost slider), **Model Health** (champion/challenger history, WAPE and bias charts, promotion
decisions, rollback), **Ledger** (prediction vs outcome), **Data Sources**, **Replay** (time
machine).

## 9. Integrations and why each is there

| Integration | Role | Why this tool |
|---|---|---|
| Oracle of the Deep (primary track) | Forecasting, governed retraining, lead-time-true weather, measured ablations | — |
| TimescaleDB / Tiger Data | Ledger-derived `forecast_errors` hypertable + `daily_forecast_error` continuous aggregate; `observations` and `traffic_observations` hypertables | Time-series facts, rolling windows, incremental aggregation |
| MongoDB Atlas | Model cards: one document per version (params, feature list, importance, evaluation, decision) | Nested, schema-changing documents read whole by version. Postgres JSONB could also hold them; we keep time series and model documents in the store built for each |
| Gemini | Morning briefing written from computed facts; never adds numbers | Language layer only — every number comes from the model |
| ElevenLabs | Reads the briefing aloud | Hands-free for a manager on the floor |
| Backboard | Persistent per-store memory of manager notes (events, standing orders, overrides) | What the manager knows that the data doesn't; informs the briefing, never changes forecasts |
| Vultr + .tech | One container serving API + dashboard at a public URL | Judges can open it; writes locked with `API_TOKEN` |
| Cursor | Code review during development (documented for SpaceXAI) | Development tool, not a runtime dependency |

## 10. Build plan (4 people, to Sunday 12:00 EDT)

| Person | Scope |
|---|---|
| A — data & weather | download, validation, Open-Meteo client + cache + cross-check, `build_features` |
| B — models & loop | stage 1/2 training, baselines, registry, retrain + promotion, replay |
| C — backend | DB schema, ledger, metrics, ingestion endpoints, API |
| D — frontend & demo | dashboard screens, demo script, Devpost write-up |

Order of work: bootstrap model with honest test WAPE (Sat AM) → API + ledger (Sat PM) → replay +
Model Health (Sat night) → polish and demo rehearsal (Sun AM).
