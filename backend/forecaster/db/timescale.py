"""TimescaleDB extras: a continuous aggregate of daily forecast error per model version / store /
category, so rolling WAPE and bias are maintained incrementally as outcomes arrive.

    python -m forecaster.db.timescale      (only meaningful when DATABASE_URL is PostgreSQL+Timescale)
"""
from __future__ import annotations

from sqlalchemy import text

from forecaster.db.schema import create_all, get_engine

# Continuous aggregates can't join, so outcomes are denormalized into a hypertable view source.
DDL = [
    """
    CREATE TABLE IF NOT EXISTS forecast_errors (
        forecast_date DATE NOT NULL,
        context TEXT NOT NULL,
        model_version TEXT NOT NULL,
        store_id TEXT NOT NULL,
        category TEXT,
        predicted DOUBLE PRECISION NOT NULL,
        actual DOUBLE PRECISION NOT NULL
    )
    """,
    "SELECT create_hypertable('forecast_errors', by_range('forecast_date'), if_not_exists => TRUE)",
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

SYNC = """
INSERT INTO forecast_errors (forecast_date, context, model_version, store_id, category, predicted, actual)
SELECT p.forecast_date, p.context, p.model_version, p.store_id, p.category, p.predicted_units, o.actual_units_sold
FROM predictions p JOIN outcomes o USING (prediction_id)
WHERE o.actual_units_sold IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM forecast_errors f
                  WHERE f.forecast_date = p.forecast_date AND f.model_version = p.model_version
                    AND f.store_id = p.store_id AND f.context = p.context)
"""

ROLLING_WAPE = """
SELECT day, sum(abs_error) OVER w / NULLIF(sum(actual_units) OVER w, 0) AS wape_30d,
       sum(signed_error) OVER w / NULLIF(sum(actual_units) OVER w, 0) AS bias_30d
FROM (SELECT day, sum(abs_error) abs_error, sum(signed_error) signed_error, sum(actual_units) actual_units
      FROM daily_forecast_error WHERE context = :ctx GROUP BY day) d
WINDOW w AS (ORDER BY day ROWS BETWEEN 29 PRECEDING AND CURRENT ROW)
ORDER BY day
"""


def main() -> None:
    engine = get_engine()
    if engine.dialect.name != "postgresql":
        raise SystemExit("DATABASE_URL must point at PostgreSQL + TimescaleDB")
    create_all(engine)
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))
        n = conn.execute(text(SYNC)).rowcount
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("CALL refresh_continuous_aggregate('daily_forecast_error', NULL, NULL)"))
        rows = conn.execute(text(ROLLING_WAPE), {"ctx": "replay"}).all()
    print(f"synced {n} error rows; {len(rows)} days in the continuous aggregate")
    if rows:
        print("latest 30-day WAPE:", rows[-1].wape_30d, "bias:", rows[-1].bias_30d)


if __name__ == "__main__":
    main()
