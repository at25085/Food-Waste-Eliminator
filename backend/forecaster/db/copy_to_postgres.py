"""Copy the SQLite database produced by the pipeline into PostgreSQL/TimescaleDB.

    python -m forecaster.db.copy_to_postgres                      # into DATABASE_URL from backend/.env
    python -m forecaster.db.copy_to_postgres postgresql+psycopg://forecaster:forecaster@localhost:5442/forecaster

The source is always the local SQLite file (never DATABASE_URL, which by then points at Postgres).
"""
from __future__ import annotations

import sys

from sqlalchemy import insert, inspect, select, text

from forecaster.config import Settings, settings
from forecaster.db import schema as S
from forecaster.db.schema import create_all, get_engine

ORDER = ["stores", "model_versions", "champion_history", "predictions", "outcomes", "traffic_observations",
         "observations", "quarantine", "anomaly_days", "promotion_experiments", "retrain_runs", "manager_notes", "uploads", "batches"]


def _has_table(conn, name: str) -> bool:
    return inspect(conn).has_table(name)


SQLITE_SOURCE = f"sqlite:///{(settings.artifacts_dir / 'forecaster.db').as_posix()}"


def main(target_url: str) -> None:
    target_url = Settings(database_url=target_url).database_url  # accepts postgres:// as Tiger shows it
    if not target_url.startswith("postgresql"):
        raise SystemExit("target must be a PostgreSQL URL")
    src = get_engine(SQLITE_SOURCE)
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
    # Rows keep their ids, so move each id sequence past the copied rows; otherwise the next insert
    # reuses id 1 ("latest by id" then picks the wrong champion) and later ones collide.
    with dst.begin() as dconn:
        for name in ORDER:
            for col in S.metadata.tables[name].primary_key.columns:
                if col.autoincrement is not False and col.type.python_type is int:
                    seq = dconn.execute(text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": name, "c": col.name}).scalar()
                    if seq:
                        dconn.execute(text(f"SELECT setval('{seq}', COALESCE((SELECT max({col.name}) FROM {name}), 0) + 1, false)"))
                        print(f"sequence {seq} advanced")
    from forecaster.db import timescale
    if timescale.enabled(dst):
        timescale.ensure(dst)
        print("timescale: synced", timescale.sync(dst), "graded forecasts into forecast_errors")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else settings.database_url)
