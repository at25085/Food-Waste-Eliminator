# WasteLess (working name — rename before submission)

Perishable-demand forecasting for grocers that **acts** on its predictions (order quantities,
waste-risk alerts, markdown suggestions) and **learns** whether they were right through a
governed champion/challenger loop. HackGT 13, primary track: Oracle of the Deep.

> "Wasteless" is an existing company (wasteless.com) selling AI markdowns to grocers. The display
> name is one setting (`APP_DISPLAY_NAME` in `.env`); the code package is `forecaster`.

## What is real and what is simulated

| Real | Simulated / labeled |
|---|---|
| Daily product sales, prices, discounts, availability — Rohlik (Czech e-grocer), 7 warehouses, perishables only, 2020–2024 | Inventory and waste (no public dataset records waste): FIFO shelf-life simulator |
| Customer traffic: each warehouse's real daily order count | The "stores" are Rohlik warehouses (an online grocer) |
| Open-Meteo weather: archived forecasts (training), forecasts issued the day before (2024 replay), live forecast (serving) | The Jan–Jun 2024 replay is a **backtest**, never presented as live customers |

## Measured results (holdout, not training data)

The bootstrap model beats both naive baselines by about a third. Real traffic helps, but
weather did **not** help this online grocer at one day ahead. We report that rather than claim it.
Exact numbers come from `artifacts/production_report.json` and the replay summary.

## Run it

```bash
# backend (Python 3.13+)
cd backend
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # (bin/pip on macOS/Linux)
PYTHONPATH=. .venv/Scripts/python -m forecaster.pipeline.run_replay        # data → features → backtest (~20 min)
PYTHONPATH=. .venv/Scripts/python -m forecaster.pipeline.train_production  # served champion + next-day plan
PYTHONPATH=. .venv/Scripts/python -m uvicorn forecaster.api.app:app --port 8000
PYTHONPATH=. .venv/Scripts/python -m pytest -q tests

# frontend
cd frontend && npm install && npm run dev
```

Data downloads automatically from the fev-bench Parquet mirror of the Rohlik Kaggle
competitions (`autogluon/fev_datasets` on HuggingFace). Weather responses are cached under
`data/cache/open_meteo/`.

### TimescaleDB (MLH Tiger Data)

```bash
docker compose up -d
PG=postgresql+psycopg://forecaster:forecaster@localhost:5442/forecaster
PYTHONPATH=. .venv/Scripts/python -m forecaster.db.copy_to_postgres $PG   # copy the pipeline's SQLite DB
DATABASE_URL=$PG PYTHONPATH=. .venv/Scripts/python -m forecaster.db.timescale  # hypertable + continuous aggregate
DATABASE_URL=$PG PYTHONPATH=. .venv/Scripts/python -m uvicorn forecaster.api.app:app --port 8000
```

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): the single feature path used for both training
and serving, the two-stage traffic → demand model, the append-only prediction → outcome ledger,
data validation and quarantine, the promotion rules, rollback, and the replay.

Weather data by [Open-Meteo.com](https://open-meteo.com) (CC BY 4.0).
