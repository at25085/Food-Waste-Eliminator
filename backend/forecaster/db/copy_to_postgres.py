"""Copy the SQLite database produced by the pipeline into PostgreSQL/TimescaleDB.

    python -m forecaster.db.copy_to_postgres postgresql+psycopg://forecaster:forecaster@localhost:5442/forecaster
"""
from __future__ import annotations

import sys

from sqlalchemy import insert, select

from forecaster.config import settings
from forecaster.db import schema as S
from forecaster.db.schema import create_all, get_engine

ORDER = ["stores", "model_versions", "champion_history", "predictions", "outcomes", "traffic_observations",
         "observations", "quarantine", "anomaly_days", "promotion_experiments", "retrain_runs", "manager_notes"]


def main(target_url: str) -> None:
    src = get_engine(settings.database_url)
    dst = get_engine(target_url)
    create_all(dst)
    for name in ORDER:
        table = S.metadata.tables[name]
        with src.connect() as sconn:
            rows = [dict(r._mapping) for r in sconn.execute(select(table))]
        if not rows:
            continue
        with dst.begin() as dconn:
            dconn.execute(table.delete())
            for i in range(0, len(rows), 5000):
                dconn.execute(insert(table), rows[i:i + 5000])
        print(f"{name}: {len(rows)} rows")


if __name__ == "__main__":
    main(sys.argv[1])
