# Freshora

**Live: https://freshora.tech**

Keeps fresh food out of the bin. Perishable-demand forecasting for grocers that **acts** on its
predictions (order quantities net of stock on the shelf, waste-risk alerts, markdowns, donation of
what a markdown won't clear) and **learns** whether they were right through a governed
champion/challenger loop. HackGT 13 — main track: **A Marina's Mission (Social Good)**.

> The display name is one setting (`APP_DISPLAY_NAME`, default "Freshora"); the code package is `forecaster`.

## What is real and what is simulated

| Real | Simulated / derived / labeled |
|---|---|
| Daily product sales, prices, seven discount types, availability — Rohlik (Czech e-grocer), 7 warehouses, fresh Bakery / Fruit and vegetables / Meat and fish, 2021–2024 | Inventory and waste (no public dataset records waste): FIFO shelf-life simulator |
| Customer traffic: each warehouse's real daily order count | **Dairy and Eggs** series are synthetic (the dataset has none): driven by each warehouse's real customer counts and calendar; reported separately |
| Open-Meteo weather: archived forecasts (training), live forecast (serving) | The **Atlanta, Macon, Augusta demo stores** replay the three largest warehouses' real sales at Georgia locations, 121 weeks later (weekdays kept), so the plan is for 2026-09-27 |
| | kg / CO2e / meals use cited factors (WRAP, ReFED) and **assumed** unit weights |

## Measured results

Headline: the **production holdout** — the served model (`demand_v10`) on the last four weeks of real
sales it never trained on (2024-05-06 → 2024-06-02, 7 warehouses, real products only). All numbers come
from generated files: [`artifacts/production_report.json`](artifacts/production_report.json) (`headline`),
[`artifacts/replay_summary.json`](artifacts/replay_summary.json),
[`artifacts/accuracy_experiments.json`](artifacts/accuracy_experiments.json),
[`artifacts/architecture_study.json`](artifacts/architecture_study.json),
[`artifacts/policy_study.json`](artifacts/policy_study.json).

| Measure | Value |
|---|---|
| **Production holdout** (headline), real products | **13.7% WAPE (86.3% accurate)** vs 22.5% same-weekday-last-week (39% less error) and 21.8% 28-day mean; bias −3.3%; P80 coverage 80% |
| By category (same holdout) | Bakery 88.8%, Fruit and vegetables 83.6%, Meat and fish 77.0% accurate |
| Synthetic Dairy / Eggs (same holdout, reported separately) | 84.0% / 84.0% accurate vs 70.7% last-week — shows the model handles them, not how real stores behave |
| Demo stores, live forecasts (4 weeks, one upload per day; each forecast saved before its day's sales) | Atlanta 86.6%, Augusta 85.1%, Macon 80.7% accurate |
| Simulated waste vs naive ordering (same rule, ~5% lost-sales service level) | ≈87% less waste **and** ≈34% fewer lost sales |
| **Replay backtest** (earlier model generation, 2024-01-20 → 2024-06-02, weekly governed retraining) | 14.4% WAPE vs 24.2% same-weekday-last-week; 10 challengers: 6 promoted, 4 rejected |
| Measured & rejected | stockout censoring, produce specialist, per-store calibration, spike weights, one multi-quantile model; weather gives no gain one day ahead |

WAPE = Σ|pred − actual| / Σ actual; "accuracy" = 1 − WAPE, by volume. The replay and the holdout are
different tests (different periods and baselines) — don't mix their numbers.
Struggles and what we learned: [docs/LESSONS.md](docs/LESSONS.md).

## Run it

```bash
# backend (Python 3.13+)
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # (bin/pip on macOS/Linux)
cp .env.example .env                                                     # optional keys; runs without them
PYTHONPATH=. .venv/Scripts/python -m forecaster.pipeline.run_replay        # data → features → backtest (~45 min)
PYTHONPATH=. .venv/Scripts/python -m forecaster.pipeline.train_production  # served champion + next-day plan (~8 min)
PYTHONPATH=. .venv/Scripts/python -m pytest -q tests
PYTHONPATH=. .venv/Scripts/python -m uvicorn forecaster.api.app:app --port 8000   # also serves frontend/dist

# frontend (dev server with hot reload; or `npm run build` and use port 8000)
cd frontend && npm install && npm run dev
```

Optional studies: `python -m forecaster.pipeline.experiment` (accuracy levers),
`python -m forecaster.pipeline.policy_study` (weather ablation + waste/lost-sales trade-off).

Data downloads automatically from the fev-bench Parquet mirror of the Rohlik Kaggle
competitions (`autogluon/fev_datasets` on HuggingFace). Weather responses are cached under
`data/cache/open_meteo/`.

### Integrations (all optional; keys go in `backend/.env`, see `.env.example`)

| Integration | Enable with | Role |
|---|---|---|
| TimescaleDB / Tiger Data | `DATABASE_URL` (Tiger Cloud) or `docker compose up -d` (port 5442) | Hypertables + continuous aggregate of forecast error |
| Gemini | `GEMINI_API_KEY` | "Ask about your store" chat and the morning briefing (Flash-Lite, 2.5 Flash fallback) |
| Backboard | `BACKBOARD_API_KEY` | Manager-note memory (API only; not in the current UI) |
| Write lock | `API_TOKEN` | Required bearer token on every write endpoint (set before deploying) |

```bash
# Tiger Data / Timescale
PG=postgresql+psycopg://forecaster:forecaster@localhost:5442/forecaster
PYTHONPATH=. .venv/Scripts/python -m forecaster.db.copy_to_postgres $PG
DATABASE_URL=$PG PYTHONPATH=. .venv/Scripts/python -m forecaster.db.timescale
```

Deployment on Vultr + .tech: [docs/DEPLOY_VULTR.md](docs/DEPLOY_VULTR.md).

## Frontend

`frontend/` (React + Vite) uses the team's design (originally an Express/EJS mock-up with
hardcoded products). Every number on it now comes from the API: Dashboard, Today's plan,
Upload (add a store by city name; one drag-and-drop box for daily and delivery sheets), Ledger, About, and a chat launcher in the corner.

## Repository layout

- `backend/` — Python API, forecasting model, pipelines (FastAPI, XGBoost)
- `frontend/` — the dashboard (React + Vite), styled after the team's original design
- `index.js`, `views/`, `public/` — the team's original Express/EJS prototype (kept for reference;
  its look now lives in `frontend/`)
- `docs/`, `scripts/`, `samples/`

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — feature path, the demand model (P50 + P80), ledger, validation,
  promotion rules, rollback, replay, measured changes, integrations
- [docs/DEMO.md](docs/DEMO.md) — 2.5-minute demo script and judge Q&A
- [docs/DEVPOST.md](docs/DEVPOST.md) — submission draft

Weather data by [Open-Meteo.com](https://open-meteo.com) (CC BY 4.0).
