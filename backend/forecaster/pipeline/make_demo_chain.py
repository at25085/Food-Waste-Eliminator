"""Demo grocery chain in Georgia: Atlanta, Macon and Augusta.

Each store replays one Rohlik warehouse's REAL daily sales, prices, discounts and customer counts
(plus that warehouse's synthetic Dairy/Eggs series) at a Georgia location, priced in dollars.
Deliveries, waste and closing stock are SIMULATED (a "last week + buffer" ordering habit through a
first-in-first-out shelf, as in make_sample_uploads). Holidays follow the source warehouse's
calendar, because that is the calendar the sales actually followed.

The history goes in as one sheet; the last four weeks go in one day at a time through the same
code path as a real upload, so every day's forecast is saved before that day's sales arrive and
is graded by the next upload — the Ledger is a real prediction-then-outcome record.

    DATABASE_URL=sqlite:///… python -m forecaster.pipeline.make_demo_chain [history|live]

Onboarding order: `history` loads each store's history, then train_production (which includes
uploaded history up to its cutoff) learns the stores, then `live` replays the four weeks after the
cutoff one day at a time — days no model version has trained on.
"""
from __future__ import annotations

import io
import sys
import time

import pandas as pd
from sqlalchemy import insert, select

from forecaster.config import REPO_ROOT, settings
from forecaster.data.prepare import processed_dir
from forecaster.pipeline.make_sample_uploads import simulate

# store → (source warehouse, city looked up for weather). The chain replays the three largest-volume
# warehouses: bigger stores have steadier day-to-day sales, as a real chain's main stores would.
CHAIN = {"Atlanta": ("Brno_1", "Atlanta, GA"), "Augusta": ("Prague_1", "Augusta, GA"), "Macon": ("Budapest_1", "Macon, GA")}
LOCAL_PER_USD = {"Brno_1": 23.0, "Prague_1": 23.0, "Budapest_1": 365.0}  # koruna / forint per dollar (2024)
SOURCE_COUNTRY = {"Brno_1": "CZ", "Prague_1": "CZ", "Budapest_1": "HU"}  # the calendar the sales followed
# Source dates (the real sales). The chain replays them 121 weeks later, so weekdays are unchanged
# and the last replayed day is 2026-09-26: tomorrow's plan is for Sunday 2026-09-27.
HISTORY_START, LIVE_START, LIVE_END = "2023-12-01", "2024-05-05", "2024-06-01"
DATE_SHIFT = pd.Timedelta(weeks=121)


def _shift(col: pd.Series) -> pd.Series:
    return (pd.to_datetime(col) + DATE_SHIFT).dt.strftime("%Y-%m-%d")


def sheets_for(source: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    panel = pd.read_parquet(processed_dir() / "panel.parquet")
    p = panel[(panel["store_id"] == source) & (panel["date"] >= HISTORY_START) & (panel["date"] <= LIVE_END)]
    p = p.sort_values(["product_id", "date"]).copy()
    p["sell_price_main"] = (p["sell_price_main"] / LOCAL_PER_USD[source]).round(2)
    daily, batches = [], []
    for pid, g in p.groupby("product_id"):
        g = g[g["sales"].notna()]
        if g.empty:
            continue
        life = settings.shelf_life_days.get(str(g["category"].iloc[0]), 3)
        d, b = simulate(g, life, str(pid))
        daily += d
        batches += b
    daily, batches = pd.DataFrame(daily), pd.DataFrame(batches)
    daily["date"] = _shift(daily["date"])
    for c in ("received_date", "expiry_date"):
        batches[c] = _shift(batches[c])
    return daily, batches


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    from forecaster.api import app as api  # the real upload path (validation, grading, planning)
    from forecaster.db import schema as S
    from forecaster.weather import open_meteo as om

    engine = api.engine
    out = REPO_ROOT / "samples" / "georgia"
    out.mkdir(parents=True, exist_ok=True)
    live_days = pd.date_range(pd.Timestamp(LIVE_START) + DATE_SHIFT, pd.Timestamp(LIVE_END) + DATE_SHIFT).strftime("%Y-%m-%d")
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    for store_id, (source, place) in CHAIN.items():
        t0 = time.time()
        if phase == "live":
            daily = pd.read_csv(out / f"{store_id}_daily.csv", dtype={"product_id": str})
            batches = pd.read_csv(out / f"{store_id}_batches.csv", dtype={"product_id": str})
            _live(api, store_id, daily, batches, live_days, t0)
            continue
        loc = om.geocode(place)
        with engine.begin() as conn:
            if conn.execute(select(S.stores.c.store_id).where(S.stores.c.store_id == store_id)).first():
                print(f"{store_id}: already exists, skipping (delete it first to rebuild)")
                continue
            conn.execute(insert(S.stores).values(
                store_id=store_id, city=loc["city"], lat=loc["lat"], lon=loc["lon"], timezone=loc["timezone"],
                country=loc["country"], subdivision=loc["subdivision"], traffic_connected=False, kind="demo",
                label=f"Demo store: real grocery sales (Rohlik {source.replace('_', ' ')}, 2023-24) replayed in "
                      f"{loc['city']} 121 weeks later; deliveries and waste simulated; dairy and eggs synthetic"))
        daily, batches = sheets_for(source)
        daily.to_csv(out / f"{store_id}_daily.csv", index=False)
        batches.to_csv(out / f"{store_id}_batches.csv", index=False)

        history = daily[daily["date"] < live_days[0]]
        r = api._ingest_daily(store_id, history.to_csv(index=False).encode(), "history.csv", None)
        print(f"{store_id} ← {source}: history {r['accepted']} rows, next forecast {r.get('next_forecast_date')}", flush=True)
        if phase == "all":
            _live(api, store_id, daily, batches, live_days, t0)


def _live(api, store_id: str, daily: pd.DataFrame, batches: pd.DataFrame, live_days, t0: float) -> None:
    """One upload per day: each day's forecast already exists when that day's sales arrive."""
    daily = daily.assign(date=pd.to_datetime(daily["date"]).dt.strftime("%Y-%m-%d"))
    api.store_learning.forecast_next_day(api.engine, store_id)  # tomorrow's plan from the current champion
    for d in live_days:
        day_rows = daily[daily["date"] == d]
        r = api._ingest_daily(store_id, day_rows.to_csv(index=False).encode(), f"{d}.csv", None)
        print(f"  {d}: {r['accepted']} rows, {r['outcomes_attached']} forecasts graded", flush=True)
    b = api._ingest_batches(store_id, batches.to_csv(index=False).encode(), "batches.csv")
    print(f"{store_id}: {b['accepted']} batches; done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
