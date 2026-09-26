"""Store registry: the built-in Rohlik warehouses (config) plus stores registered at runtime
(owners who upload their own data, and the labeled sample store)."""
from __future__ import annotations

from sqlalchemy import select

from forecaster.config import STORE_LOCATIONS
from forecaster.db import schema as S


def location(engine, store_id: str) -> dict | None:
    if store_id in STORE_LOCATIONS:
        return STORE_LOCATIONS[store_id]
    if engine is None:
        return None
    with engine.connect() as conn:
        r = conn.execute(select(S.stores).where(S.stores.c.store_id == store_id)).first()
    if not r or r.lat is None:
        return None
    return {"city": r.city, "lat": r.lat, "lon": r.lon, "tz": r.timezone,
            "country": r.country, "subdiv": r.subdivision}


def all_locations(engine) -> dict[str, dict]:
    out = dict(STORE_LOCATIONS)
    if engine is not None:
        with engine.connect() as conn:
            for r in conn.execute(select(S.stores)):
                if r.store_id not in out and r.lat is not None:
                    out[r.store_id] = {"city": r.city, "lat": r.lat, "lon": r.lon, "tz": r.timezone,
                                       "country": r.country, "subdiv": r.subdivision}
    return out
