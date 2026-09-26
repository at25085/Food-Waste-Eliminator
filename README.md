# WasteLess (working name — rename before submission)

Keeps fresh food out of the bin. Perishable-demand forecasting for grocers that **acts** on its
predictions (order quantities net of stock on the shelf, waste-risk alerts, markdowns, donation of
what a markdown won't clear) and **learns** whether they were right through a governed
champion/challenger loop. HackGT 13 — main track: **A Marina's Mission (Social Good)**.

> "Wasteless" is an existing company (wasteless.com) selling AI markdowns to grocers. The display
> name is one setting (`APP_DISPLAY_NAME` in `backend/.env`); the code package is `forecaster`.

## What is real and what is simulated

| Real | Simulated / derived / labeled |
|---|---|
| Daily product sales, prices, seven discount types, availability — Rohlik (Czech e-grocer), 7 warehouses, perishables only, 2020–2024 | Inventory and waste (no public dataset records waste): FIFO shelf-life simulator |
| Customer traffic: each warehouse's real daily order count | The "stores" are Rohlik warehouses (an online grocer); ~109 sampled fresh products per store |
| Open-Meteo weather: archived forecasts (training), forecasts issued the day before (2024 replay), live forecast (serving) | The Jan–Jun 2024 replay is a **backtest**, never presented as live customers |
| | kg / CO2e / meals use cited factors (WRAP, ReFED) and **assumed** unit weights |

## Measured results (data the model never trained on)

All numbers are generated files, not hand-typed:
[`artifacts/production_report.json`](artifacts/production_report.json) (`headline`),
`artifacts/replay_summary.json`, `artifacts/accuracy_experiments.json`, `artifacts/policy_study.json`.

| Measure | Value |
|---|---|
| Replay WAPE, 2024-01-20 → 2024-06-02 (with weekly governed retraining) | **14.3%** vs 24.2% same-weekday-last-week (~41% less error) |
| Production holdout WAPE, 2024-05-06 → 2024-06-02 | 14.3% vs 22.5% last-week, 21.8% 28-day mean; bias −3.1%; P80 coverage 80% |
| Simulated waste vs naive ordering (same rule, ~5% lost-sales service level) | ≈87% less waste **and** ≈26% fewer lost sales |
| Replay governance | 10 challengers: 6 promoted, 4 rejected |
| Measured & rejected | stockout censoring, produce specialist, per-store calibration, spike weights; weather gives no gain at D+1 |

WAPE = Σ|pred − actual| / Σ actual. If "accuracy" is quoted it means 1 − WAPE (≈86% by volume).

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
| MongoDB Atlas | `MONGODB_URI`, then `python -m forecaster.db.model_cards` | Model cards |
| Gemini | `GEMINI_API_KEY` | Morning briefing text |
| ElevenLabs | `ELEVENLABS_API_KEY` | Briefing audio |
| Backboard | `BACKBOARD_API_KEY` | Manager-note memory |
| Write lock | `API_TOKEN` | Required bearer token on every write endpoint (set before deploying) |

```bash
# Tiger Data / Timescale
PG=postgresql+psycopg://forecaster:forecaster@localhost:5442/forecaster
PYTHONPATH=. .venv/Scripts/python -m forecaster.db.copy_to_postgres $PG
DATABASE_URL=$PG PYTHONPATH=. .venv/Scripts/python -m forecaster.db.timescale
```

Deployment on Vultr + .tech: [docs/DEPLOY_VULTR.md](docs/DEPLOY_VULTR.md).

## Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — feature path, two-stage model, ledger, validation,
  promotion rules, rollback, replay, measured changes, integrations
- [docs/DEMO.md](docs/DEMO.md) — 2.5-minute demo script and judge Q&A
- [docs/DEVPOST.md](docs/DEVPOST.md) — submission draft
- [docs/SPONSOR_OPTIONS.md](docs/SPONSOR_OPTIONS.md) — Visa "Last Call" and Meta "Community Table" designs

Weather data by [Open-Meteo.com](https://open-meteo.com) (CC BY 4.0).
