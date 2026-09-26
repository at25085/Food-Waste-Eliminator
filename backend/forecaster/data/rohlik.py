"""Download and load the Rohlik bootstrap dataset (real e-grocer data, perishables only).

Source: Kaggle "Rohlik Sales Forecasting Challenge v2" and "Rohlik Orders Forecasting Challenge",
mirrored as Parquet in the fev-bench collection (autogluon/fev_datasets on HuggingFace).
"""
from __future__ import annotations

from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from forecaster.config import settings

HF_BASE = "https://huggingface.co/datasets/autogluon/fev_datasets/resolve/main"
FILES = {
    "rohlik_sales_1D.parquet": f"{HF_BASE}/rohlik_sales/1D/train-00000-of-00001.parquet",
    "rohlik_orders_1D.parquet": f"{HF_BASE}/rohlik_orders/1D/train-00000-of-00001.parquet",
}

DISCOUNT_COLS = [f"type_{i}_discount" for i in range(7)]


def raw_dir() -> Path:
    return settings.data_dir / "raw"


def download(force: bool = False) -> None:
    raw_dir().mkdir(parents=True, exist_ok=True)
    for name, url in FILES.items():
        dest = raw_dir() / name
        if dest.exists() and not force:
            continue
        with httpx.stream("GET", url, follow_redirects=True, timeout=300) as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)


def _explode(df: pd.DataFrame, static: list[str]) -> pd.DataFrame:
    """fev format stores each series as one row of equal-length arrays; make it long."""
    array_cols = [c for c in df.columns if c not in static]
    lengths = df["timestamp"].map(len).to_numpy()
    out = {c: np.repeat(df[c].to_numpy(), lengths) for c in static}
    for c in array_cols:
        out[c] = np.concatenate(df[c].to_numpy())
    long = pd.DataFrame(out)
    long["date"] = pd.to_datetime(long.pop("timestamp")).dt.normalize()
    return long


def load_sales() -> pd.DataFrame:
    df = pd.read_parquet(raw_dir() / "rohlik_sales_1D.parquet")
    static = ["id", "product_unique_id", "name", "L1_category_name_en", "L2_category_name_en",
              "L3_category_name_en", "L4_category_name_en", "warehouse"]
    long = _explode(df, static)
    long = long.rename(columns={
        "id": "series_id",
        "L1_category_name_en": "category",
        "L2_category_name_en": "category_l2",
        "L3_category_name_en": "category_l3",
        "L4_category_name_en": "category_l4",
        "warehouse": "store_id",
        "total_orders": "customer_count",
    })
    long["product_id"] = long["product_unique_id"].astype("int64").astype(str)
    long = long.drop(columns=["product_unique_id"])
    for c in ["sales", "sell_price_main", "availability", "customer_count", *DISCOUNT_COLS,
              "holiday", "shops_closed", "winter_school_holidays", "school_holidays"]:
        long[c] = long[c].astype("float64")
    long["discount_max"] = long[DISCOUNT_COLS].max(axis=1)
    return long.sort_values(["store_id", "product_id", "date"]).reset_index(drop=True)


def load_orders() -> pd.DataFrame:
    """Warehouse-day orders plus the dataset's own weather (kept, renamed, never overwritten)."""
    df = pd.read_parquet(raw_dir() / "rohlik_orders_1D.parquet")
    long = _explode(df, ["id"]).rename(columns={
        "id": "store_id",
        "precipitation": "dataset_precipitation",
        "snow": "dataset_snow",
    })
    num = [c for c in long.columns if c not in ("store_id", "date", "holiday_name")]
    long[num] = long[num].astype("float64")
    return long.sort_values(["store_id", "date"]).reset_index(drop=True)


def traffic_from_sales(sales: pd.DataFrame) -> pd.DataFrame:
    """Store-day customer counts. total_orders is repeated on every product row; take one value."""
    t = (sales.dropna(subset=["customer_count"])
         .groupby(["store_id", "date"], as_index=False)["customer_count"].median())
    return t
