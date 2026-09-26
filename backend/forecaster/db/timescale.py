"""TimescaleDB (Tiger Data): forecast error as a time series the database maintains itself.

Every graded forecast (a prediction with its real outcome) is copied into the `forecast_errors`
hypertable; the `daily_forecast_error` continuous aggregate keeps daily error per context / model
version / store / category up to date. The dashboard's error charts read that aggregate when the
app runs on Timescale, so rolling WAPE and bias come from the database, not from Python.

This runs automatically (after uploads, graded outcomes, retrains and pipeline runs). The CLI does a
one-off setup + sync, e.g. after copying a SQLite database into Postgres:

    python -m forecaster.db.timescale
"""
from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy import text

log = logging.getLogger(__name__)

# Continuous aggregates can't join, so graded forecasts are denormalized into a hypertable.
DDL = [
    """
    CREATE TABLE IF NOT EXISTS forecast_errors (
        prediction_id TEXT NOT NULL,
        forecast_date DATE NOT NULL,
        context TEXT NOT NULL,
        model_version TEXT NOT NULL,
        store_id TEXT NOT NULL,
        category TEXT,
        predicted DOUBLE PRECISION NOT NULL,
        actual DOUBLE PRECISION NOT NULL
    )
    """,
    "ALTER TABLE forecast_errors ADD COLUMN IF NOT EXISTS prediction_id TEXT",
    "SELECT create_hypertable('forecast_errors', by_range('forecast_date'), if_not_exists => TRUE)",
    "CREATE INDEX IF NOT EXISTS forecast_errors_prediction ON forecast_errors (prediction_id, forecast_date)",
    """
    CREATE MATERIALIZED VIEW IF NOT EXISTS daily_forecast_error
    WITH (timescaledb.continuous) AS
    SELECT time_bucket(INTERVAL '1 day', forecast_date) AS day, context, model_version, store_id, category,
           sum(abs(predicted - actual)) AS abs_error,
           sum(predicted - actual)      AS signed_error,
           sum(actual)                  AS actual_units,
           count(*)                     AS n
    FROM forecast_errors
    GROUP BY day, context, model_version, store_id, category
    WITH NO DATA
    """,
    """
    SELECT add_continuous_aggregate_policy('daily_forecast_error',
        start_offset => NULL, end_offset => INTERVAL '1 day', schedule_interval => INTERVAL '1 hour',
        if_not_exists => TRUE)
    """,
]

# Only graded forecasts not copied yet (by prediction_id — predictions are append-only).
SYNC = """
INSERT INTO forecast_errors (prediction_id, forecast_date, context, model_version, store_id, category, predicted, actual)
SELECT p.prediction_id, p.forecast_date, p.context, p.model_version, p.store_id, p.category,
       p.predicted_units, o.actual_units_sold
FROM predictions p JOIN outcomes o ON o.prediction_id = p.prediction_id
WHERE o.actual_units_sold IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM forecast_errors f WHERE f.prediction_id = p.prediction_id)
"""

ROLLING_WAPE = """
SELECT day, sum(abs_error) OVER w / NULLIF(sum(actual_units) OVER w, 0) AS wape_30d,
       sum(signed_error) OVER w / NULLIF(sum(actual_units) OVER w, 0) AS bias_30d
FROM (SELECT day, sum(abs_error) abs_error, sum(signed_error) signed_error, sum(actual_units) actual_units
      FROM daily_forecast_error WHERE context = :ctx GROUP BY day) d
WINDOW w AS (ORDER BY day ROWS BETWEEN 29 PRECEDING AND CURRENT ROW)
ORDER BY day
"""


def enabled(engine) -> bool:
    if engine.dialect.name != "postgresql":
        return False
    with engine.connect() as conn:
        return bool(conn.execute(text("SELECT count(*) FROM pg_extension WHERE extname='timescaledb'")).scalar())


def ensure(engine) -> None:
    """Create the hypertable and continuous aggregate if missing (idempotent)."""
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))


def sync(engine) -> int:
    """Copy newly graded forecasts in and refresh the aggregate. Returns rows added."""
    with engine.begin() as conn:
        n = conn.execute(text(SYNC)).rowcount
    if n:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("CALL refresh_continuous_aggregate('daily_forecast_error', NULL, NULL)"))
    return n


def sync_safe(engine) -> int | None:
    """Best-effort: never let the analytics copy break an upload or a retrain."""
    try:
        return sync(engine) if enabled(engine) else None
    except Exception as e:  # noqa: BLE001
        log.warning("Timescale sync failed: %s", e)
        return None


def daily_series(engine, context: str, store: str | None = None) -> pd.DataFrame:
    """Daily error totals from the continuous aggregate, with the version that served most units."""
    sql = """
        SELECT day, model_version, sum(abs_error) abs_err, sum(signed_error) err, sum(actual_units) actual
        FROM daily_forecast_error WHERE context = :ctx {store_filter}
        GROUP BY day, model_version ORDER BY day
    """.format(store_filter="AND store_id = :store" if store else "")
    with engine.connect() as conn:
        df = pd.read_sql(text(sql), conn, params={"ctx": context, "store": store})
    if df.empty:
        return df
    top = df.sort_values("actual", ascending=False).drop_duplicates("day").set_index("day")["model_version"]
    d = df.groupby("day", as_index=False)[["abs_err", "err", "actual"]].sum()
    d["version"] = d["day"].map(top)
    return d.rename(columns={"day": "forecast_date"})


def main() -> None:
    from forecaster.db.schema import create_all, get_engine
    engine = get_engine()
    if not enabled(engine):
        raise SystemExit("DATABASE_URL must point at PostgreSQL with the timescaledb extension")
    create_all(engine)
    ensure(engine)
    n = sync(engine)
    with engine.connect() as conn:
        rows = conn.execute(text(ROLLING_WAPE), {"ctx": "replay"}).all()
    print(f"synced {n} graded forecasts; {len(rows)} days in the continuous aggregate")
    if rows:
        print("latest 30-day WAPE:", rows[-1].wape_30d, "bias:", rows[-1].bias_30d)


if __name__ == "__main__":
    main()
