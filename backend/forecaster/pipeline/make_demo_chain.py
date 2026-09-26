"""Demo grocery chain in Georgia: Atlanta, Macon and Augusta.

Each store replays one Rohlik warehouse's REAL daily sales, prices, discounts and customer counts
(plus that warehouse's synthetic Dairy/Eggs series) at a Georgia location, priced in dollars.
Deliveries, waste and closing stock are SIMULATED (a "last week + buffer" ordering habit through a
first-in-first-out shelf, as in make_sample_uploads). Holidays follow the source warehouse's
calendar, because that is the calendar the sales actually followed.

The history goes in as one sheet; the last four weeks go in one day at a time through the same
code path as a real upload, so every day's forecast is saved before that day's sales arrive and
is graded by the next upload — the Ledger is a real prediction-then-outcome record.

    DATABASE_URL=sqlite:///… python -m forecaster.pipeline.make_demo_chain
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

# store → (source warehouse, city looked up for weather)
CHAIN = {"Atlanta": ("Brno_1", "Atlanta, GA"), "Augusta": ("Prague_2", "Augusta, GA"), "Macon": ("Prague_3", "Macon, GA")}
CZK_PER_USD = 23.0  # the three source warehouses price in Czech koruna
HISTORY_START, LIVE_START, LIVE_END = "2023-12-01", "2024-05-06", "2024-06-02"


def sheets_for(source: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    panel = pd.read_parquet(processed_dir() / "panel.parquet")
    p = panel[(panel["store_id"] == source) & (panel["date"] >= HISTORY_START) & (panel["date"] <= LIVE_END)]
    p = p.sort_values(["product_id", "date"]).copy()
    p["sell_price_main"] = (p["sell_price_main"] / CZK_PER_USD).round(2)
    daily, batches = [], []
    for pid, g in p.groupby("product_id"):
        g = g[g["sales"].notna()]
        if g.empty:
            continue
        life = settings.shelf_life_days.get(str(g["category"].iloc[0]), 3)
        d, b = simulate(g, life, str(pid))
        daily += d
        batches += b
    return pd.DataFrame(daily), pd.DataFrame(batches)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    from forecaster.api import app as api  # the real upload path (validation, grading, planning)
    from forecaster.db import schema as S
    from forecaster.weather import open_meteo as om

    engine = api.engine
    out = REPO_ROOT / "samples" / "georgia"
    out.mkdir(parents=True, exist_ok=True)
    live_days = pd.date_range(LIVE_START, LIVE_END).strftime("%Y-%m-%d")
    for store_id, (source, place) in CHAIN.items():
        t0 = time.time()
        loc = om.geocode(place)
        with engine.begin() as conn:
            if conn.execute(select(S.stores.c.store_id).where(S.stores.c.store_id == store_id)).first():
                print(f"{store_id}: already exists, skipping (delete it first to rebuild)")
                continue
            conn.execute(insert(S.stores).values(
                store_id=store_id, city=loc["city"], lat=loc["lat"], lon=loc["lon"], timezone=loc["timezone"],
                country="CZ", subdivision=None, traffic_connected=False, kind="demo",
                label=f"Demo store: real grocery sales (Rohlik {source.replace('_', ' ')}) replayed in {loc['city']}; "
                      "deliveries and waste simulated; dairy and eggs synthetic"))
        daily, batches = sheets_for(source)
        daily.to_csv(out / f"{store_id}_daily.csv", index=False)
        batches.to_csv(out / f"{store_id}_batches.csv", index=False)

        history = daily[daily["date"] < LIVE_START]
        r = api._ingest_daily(store_id, history.to_csv(index=False).encode(), "history.csv", None)
        print(f"{store_id} ← {source}: history {r['accepted']} rows, next forecast {r.get('next_forecast_date')}", flush=True)
        for d in live_days:
            day_rows = daily[daily["date"] == d]
            r = api._ingest_daily(store_id, day_rows.to_csv(index=False).encode(), f"{d}.csv", None)
            print(f"  {d}: {r['accepted']} rows, {r['outcomes_attached']} forecasts graded", flush=True)
        b = api._ingest_batches(store_id, batches.to_csv(index=False).encode(), "batches.csv")
        print(f"{store_id}: {b['accepted']} batches; done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
