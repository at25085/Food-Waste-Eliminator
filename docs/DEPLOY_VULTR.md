# Going live: Vultr (app) + Tiger Cloud (database) + .tech (address)

```
 browser ─► https://yourname.tech ─► Vultr server
                                      ├─ Caddy (HTTPS)
                                      └─ app container: API + dashboard + the trained models
                                           ├─ reads models from /opt/forecaster/artifacts (persistent)
                                           └─ DATABASE_URL ─► Tiger Cloud (Postgres + TimescaleDB), the only database
```

The trained models live on the Vultr server's disk (a Docker volume), because the server runs
them: an owner's upload is validated, saved to Tiger Cloud, run through the model, and the plan is
saved back. Retrains made on the server write new model versions to the same volume.

## 1. Accounts (you)
- **Tiger Cloud** service → connection string; paste it into `DATABASE_URL` exactly as shown.
- **Vultr** Cloud Compute, Ubuntu. 4 vCPU / 8 GB if you want to press "Retrain" live
  (a retrain takes ~6–8 min and 2–3 GB RAM); 2 vCPU / 4 GB is enough to only serve plans.
  Add your SSH key when creating it.
- **.tech domain** (MLH code) → A record → the server's IP.

## 2. `backend/.env`
```
DATABASE_URL=postgresql+psycopg://…tiger cloud…
GEMINI_API_KEY=…  BACKBOARD_API_KEY=…
API_TOKEN=<long random string>         # locks uploads/retrain/rollback; open the dashboard once with ?token=…
APP_DISPLAY_NAME=Freshora              # optional; this is the default
DOMAIN=yourname.tech
```

## 3. Move the data into Tiger Cloud (once, from the laptop)
```bash
cd backend
PYTHONPATH=. .venv/Scripts/python -m forecaster.db.copy_to_postgres "$DATABASE_URL"
```
This copies every table, creates the Timescale hypertables and the continuous aggregate, and
syncs all graded forecasts. After this the app never uses SQLite.

## 4. Deploy (one command)
```bash
./scripts/deploy_vultr.sh root@SERVER_IP
```
Installs Docker if needed, copies the code, the served models (`demand_v*`), plans, reports and
training data, then builds and starts the app + Caddy. HTTPS is automatic once the domain points at
the server. Re-run the same command to redeploy; the volumes (models, plans) are kept.

## End-to-end check (any URL)
```bash
python scripts/e2e_check.py https://yourname.tech --token "$API_TOKEN"      # add --retrain for the slow part
```
Creates a test store `E2E_Test`, uploads the sample sheets, and checks plans, CSVs, Timescale,
Gemini (briefing + store chat), model cards and the write lock (PASS/FAIL per step). It writes to the
database, so reset afterwards by re-running step 3.

## Checks after deploy
- `https://yourname.tech/api/integrations` → `"database": "postgresql"`, `timescale.healthy: true`
- Dashboard → "Forecast vs. what sold" loads; `/api/metrics/timeseries` reports `source: timescale continuous aggregate`.
