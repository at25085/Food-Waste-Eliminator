"""Build labeled sample upload sheets from real Rohlik Prague_1 history.

Sales, prices, discounts and customer counts are real. Deliveries, waste and closing stock are
SIMULATED: a legacy "last week + buffer" ordering habit through a first-in-first-out shelf,
topped up so that recorded sales are never cut. Each simulated delivery is tracked as its own
batch, so the batch sheet and the daily sheets describe the same stock.

    samples/1_history_import.csv   2024-03-01 → 2024-05-05  (the serving model trained on these dates)
    samples/2_week_may06.csv       2024-05-06 → 2024-05-19  (never seen by the serving model)
    samples/3_week_may20.csv       2024-05-20 → 2024-06-02  (never seen by the serving model)
    samples/4_batches.csv          every delivery 2024-03-01 → 2024-06-02 with its expiry date

    python -m forecaster.pipeline.make_sample_uploads
"""
from __future__ import annotations

import pandas as pd

from forecaster.config import REPO_ROOT, settings
from forecaster.data.prepare import processed_dir

SOURCE_STORE = "Prague_1"
START = "2024-03-01"
SPLITS = [("1_history_import.csv", "2024-03-01", "2024-05-05"),
          ("2_week_may06.csv", "2024-05-06", "2024-05-19"),
          ("3_week_may20.csv", "2024-05-20", "2024-06-02")]


def simulate(g: pd.DataFrame, life: int, pid: str):
    """Per-batch FIFO shelf. Returns (daily rows, batch rows)."""
    open_batches: list[dict] = []  # oldest first
    daily, closed = [], []
    hist = g["sales"].shift(7).bfill().fillna(g["sales"].mean())
    buffer = 1.28 * g["sales"].rolling(7, min_periods=2).std().shift(1).fillna(0)
    for (_, r), base, buf in zip(g.iterrows(), hist, buffer):
        day = r["date"]
        # 1. expire batches whose last sellable day has passed
        wasted = 0.0
        for b in [b for b in open_batches if b["expiry"] < day]:
            b["wasted"] = b["left"]
            wasted += b["left"]
            b["left"] = 0.0
            closed.append(b)
        open_batches = [b for b in open_batches if b["expiry"] >= day]
        # 2. order: legacy habit, topped up so recorded sales are never cut
        on_hand = sum(b["left"] for b in open_batches)
        order = max(base + buf - on_hand, 0.0) + max(r["sales"] - max(base + buf, on_hand), 0.0)
        if order > 1e-9:
            open_batches.append({"batch_id": f"P1-{pid}-{day:%m%d}", "product_id": pid, "product_name": r["name"],
                                 "category": r["category"], "received_date": day,
                                 "expiry": day + pd.Timedelta(days=life - 1),
                                 "quantity": order, "left": order, "sold": 0.0, "wasted": 0.0})
        # 3. sell oldest first
        remaining = r["sales"]
        for b in open_batches:
            take = min(b["left"], remaining)
            b["left"] -= take
            b["sold"] += take
            remaining -= take
        sold = r["sales"] - remaining
        daily.append({"date": day.strftime("%Y-%m-%d"), "product_id": pid, "product_name": r["name"],
                      "category": r["category"], "units_received": round(order, 2), "units_sold": round(sold, 2),
                      "units_wasted": round(wasted, 2), "price": r["sell_price_main"],
                      "discount": float(r.get("discount_max", 0) or 0),
                      "stock_end": round(sum(b["left"] for b in open_batches), 2),
                      "customers": r["customer_count"], "shelf_life_days": life})
    batch_rows = []
    for b in closed + open_batches:
        still_open = b in open_batches and b["left"] > 1e-9
        batch_rows.append({"batch_id": b["batch_id"], "product_id": pid, "product_name": b["product_name"],
                           "category": b["category"], "received_date": b["received_date"].strftime("%Y-%m-%d"),
                           "quantity": round(b["quantity"], 2), "expiry_date": b["expiry"].strftime("%Y-%m-%d"),
                           "sold": round(b["sold"], 2), "wasted": None if still_open else round(b["wasted"], 2)})
    return daily, batch_rows


def main() -> None:
    panel = pd.read_parquet(processed_dir() / "panel.parquet")
    p = panel[(panel["store_id"] == SOURCE_STORE) & (panel["date"] >= START)].sort_values(["product_id", "date"])
    daily_rows, batch_rows = [], []
    for pid, g in p.groupby("product_id"):
        g = g[g["sales"].notna()]
        if g.empty:
            continue
        life = settings.shelf_life_days.get(str(g["category"].iloc[0]), 3)
        d, b = simulate(g, life, str(pid))
        daily_rows += d
        batch_rows += b
    df = pd.DataFrame(daily_rows)
    out = REPO_ROOT / "samples"
    out.mkdir(exist_ok=True)
    for name, lo, hi in SPLITS:
        part = df[(df["date"] >= lo) & (df["date"] <= hi)]
        part.to_csv(out / name, index=False)
        print(name, len(part), "rows")
    pd.DataFrame(batch_rows).to_csv(out / "4_batches.csv", index=False)
    print("4_batches.csv", len(batch_rows), "batches")
    (out / "README.md").write_text(
        "# Sample upload sheets\n\nBuilt from real Rohlik Prague_1 history: **sales, prices, discounts and customer "
        "counts are real; deliveries, waste and closing stock are simulated** (a legacy \"last week + buffer\" "
        "ordering habit through a first-in-first-out shelf; each delivery is tracked as its own batch, so the "
        "batch sheet and the daily sheets describe the same stock). Upload them to a store labeled as a sample "
        "store. File 1 covers dates the serving model trained on (history only); files 2 and 3 are dates it never "
        "saw, so the grading they trigger is honest. File 4 is the optional batch sheet.\n")


if __name__ == "__main__":
    main()
