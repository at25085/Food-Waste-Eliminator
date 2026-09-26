# Dashboard (frontend)

Vite + React + TypeScript + Recharts. It talks to the FastAPI backend in `backend/` via `/api`.
The product name shown in the UI comes from `GET /api/meta` (`app_display_name`), so a rename
needs only the backend setting (`APP_DISPLAY_NAME`).

## Run

1. Start the API (Git Bash, from `backend/`):

   ```bash
   PYTHONPATH=. ./.venv/Scripts/python -m uvicorn forecaster.api.app:app --port 8000
   ```

2. Start the dashboard (from `frontend/`):

   ```bash
   npm install
   npm run dev          # http://localhost:5173, proxies /api → http://127.0.0.1:8000
   ```

   To point at a different backend, set `API_TARGET`, e.g. `API_TARGET=http://127.0.0.1:9000 npm run dev`.

3. Production build: `npm run build` (type-checks, then writes `dist/`). `npm run preview` serves `dist/` with the same proxy.

## Screens

| Screen | Endpoints | Notes |
|---|---|---|
| **Today's plan** | `/api/stores`, `/api/stores/{id}/recommendations?waste_cost_ratio=`, `/api/stores/{id}/weather/live`, `/api/promotions` (GET/POST) | Store picker, plan basis, live Open-Meteo 7-day card, waste-vs-stockout slider (re-queries, shows per-item order deltas), item table with category/risk filters and sorting, "Accept" markdown → promotion experiment. |
| **Model health** | `/api/model-health?context=`, `/api/production/report`, `/api/data-sources?store=`, `/api/anomaly-days?store=`, `POST /api/traffic/csv` | Champion, test vs train WAPE, first-party data progress, holdout comparison vs baselines/ablations, segments, simulated waste (labeled), weather cross-check, feature importance, data-source checklist with traffic CSV upload, unusual days. |
| **Learning loop** | `/api/metrics/timeseries?context=replay`, `/api/retrain-runs?context=replay`, `/api/models?context=replay`, `/api/metrics?context=replay&window=&by=` | Labeled historical replay. Play button animates the backtest day by day: version bands, serving badge flips on promotion, toasts on promotion or rejection, decision feed with per-category checks. |
| **Ledger** | `/api/ledger?context=&store=&product=&limit=` | Predictions next to outcomes; "awaiting outcome" when none is attached yet. |

## Empty and pending states

While the pipeline is running, several endpoints return 404 (`recommendations.parquet`,
`production_report.json`, production champion). Each screen shows what's missing and the command
that produces it, with a **Check again** button. The Learning loop has **Refresh data** because the
replay writes to the database progressively.

## Honesty labels

- Inventory and waste are simulated; forecasts are real model output (Today, rail footer).
- The replay is always labeled a historical backtest, never live customers.
- Ablations (weather, traffic) are reported as measured, including when a feature didn't help.
