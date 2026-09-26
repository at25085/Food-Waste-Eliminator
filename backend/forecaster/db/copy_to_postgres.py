"""Copy the SQLite database produced by the pipeline into PostgreSQL/TimescaleDB.

    python -m forecaster.db.copy_to_postgres postgresql+psycopg://forecaster:forecaster@localhost:5442/forecaster
"""
from __future__ import annotations

import sys

from sqlalchemy import insert, inspect, select, text

from forecaster.config import settings
from forecaster.db import schema as S
from forecaster.db.schema import create_all, get_engine

ORDER = ["stores", "model_versions", "champion_history", "predictions", "outcomes", "traffic_observations",
         "observations", "quarantine", "anomaly_days", "promotion_experiments", "retrain_runs", "manager_notes", "uploads", "batches"]


def _has_table(conn, name: str) -> bool:
    return inspect(conn).has_table(name)


def main(target_url: str) -> None:
    src = get_engine(settings.database_url)
    dst = get_engine(target_url)
    create_all(dst)
    with dst.begin() as dconn:  # clear children before parents, then copy parents before children
        for name in reversed(ORDER):
            dconn.execute(S.metadata.tables[name].delete())
        if dst.dialect.name == "postgresql":
            dconn.execute(text("DELETE FROM forecast_errors WHERE true")) if _has_table(dconn, "forecast_errors") else None
    for name in ORDER:
        table = S.metadata.tables[name]
        with src.connect() as sconn:
            rows = [dict(r._mapping) for r in sconn.execute(select(table))]
        if not rows:
            continue
        with dst.begin() as dconn:
            for i in range(0, len(rows), 5000):
                dconn.execute(insert(table), rows[i:i + 5000])
        print(f"{name}: {len(rows)} rows")
    from forecaster.db import timescale
    if timescale.enabled(dst):
        timescale.ensure(dst)
        print("timescale: synced", timescale.sync(dst), "graded forecasts into forecast_errors")


if __name__ == "__main__":
    main(sys.argv[1])
