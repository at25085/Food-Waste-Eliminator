"""Seed reference rows (stores)."""
from __future__ import annotations

from sqlalchemy import insert, select

from forecaster.config import STORE_LOCATIONS
from forecaster.db import schema as S


# Operational incidents recorded in the Rohlik data → how training treats those days.
ANOMALY_POLICY = {"shutdown": "exclude", "blackout": "exclude",
                  "mini_shutdown": "keep", "frankfurt_shutdown": "keep"}


def seed_anomaly_days(engine) -> None:
    """Flag (never delete) unusual days. 'exclude' = demand was not properly observed that day."""
    from forecaster.data import rohlik
    orders = rohlik.load_orders()
    rows = {}
    for col, policy in ANOMALY_POLICY.items():
        for _, r in orders[orders[col] > 0].iterrows():
            key = (r["store_id"], r["date"].date())
            prev = rows.get(key)
            if prev is None or policy == "exclude":
                rows[key] = dict(store_id=key[0], date=key[1], is_anomaly=True, reason=col, training_policy=policy)
    with engine.begin() as conn:
        conn.execute(S.anomaly_days.delete())
        if rows:
            conn.execute(insert(S.anomaly_days), list(rows.values()))


def excluded_days(engine):
    import pandas as pd
    with engine.connect() as conn:
        df = pd.read_sql(select(S.anomaly_days.c.store_id, S.anomaly_days.c.date)
                         .where(S.anomaly_days.c.training_policy == "exclude"), conn)
    df["date"] = pd.to_datetime(df["date"])
    return df


def seed_stores(engine, traffic_connected: bool = True) -> None:
    """Rohlik warehouses carry a real per-day order count, so their traffic feed is connected."""
    with engine.begin() as conn:
        existing = {r[0] for r in conn.execute(select(S.stores.c.store_id))}
        rows = [dict(store_id=s, city=l["city"], lat=l["lat"], lon=l["lon"], timezone=l["tz"],
                     traffic_connected=traffic_connected, kind="rohlik", country=l["country"],
                     subdivision=l["subdiv"], label="Rohlik warehouse — real sales history (online grocer)")
                for s, l in STORE_LOCATIONS.items() if s not in existing]
        if rows:
            conn.execute(insert(S.stores), rows)
        for s, l in STORE_LOCATIONS.items():  # label the built-in warehouses (also fixes older databases)
            conn.execute(S.stores.update().where((S.stores.c.store_id == s) & S.stores.c.kind.is_(None)).values(
                kind="rohlik", country=l["country"], subdivision=l["subdiv"],
                label="Rohlik warehouse — real sales history (online grocer)"))
