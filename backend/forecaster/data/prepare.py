"""Build the processed panel used for bootstrap training, replay and serving."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from forecaster.config import STORE_LOCATIONS, settings
from forecaster.data import rohlik, synthetic
from forecaster.data.validation import validate_observations
from forecaster.features.build import complete_daily_index
from forecaster.weather import open_meteo as om

HISTORY_START = pd.Timestamp("2021-12-01")  # lags need a month before training starts (2022-01)
DATA_END = pd.Timestamp("2024-06-02")


def processed_dir():
    d = settings.data_dir / "processed"
    d.mkdir(parents=True, exist_ok=True)
    return d


def select_series(sales: pd.DataFrame, per_store: int, seed: int = 13) -> pd.Series:
    """Series with ≥1 year of history that are still active at the end of the data, sampled
    per store and stratified by category so every store keeps all three perishable groups."""
    obs = sales.dropna(subset=["sales"])
    stats = obs.groupby(["store_id", "product_id"]).agg(
        n=("sales", "size"), end=("date", "max"), category=("category", "first"),
        volume=("sales", "mean")).reset_index()
    eligible = stats[(stats["n"] >= 365) & (stats["end"] >= DATA_END - pd.Timedelta(days=30))]
    rng = np.random.default_rng(seed)
    picked = []
    for store, g in eligible.groupby("store_id"):
        shares = g["category"].value_counts(normalize=True)
        for cat, share in shares.items():
            pool = g[g["category"] == cat]
            k = min(len(pool), max(3, int(round(per_store * share))))
            picked.append(pool.iloc[rng.choice(len(pool), size=k, replace=False)])
    sel = pd.concat(picked)
    return sel["store_id"] + "|" + sel["product_id"]


def store_weather(start: date, end: date) -> pd.DataFrame:
    frames = []
    for store_id, loc in STORE_LOCATIONS.items():
        w = om.training_weather(loc["lat"], loc["lon"], start, end, loc["tz"])
        w["store_id"] = store_id
        frames.append(w)
    return pd.concat(frames, ignore_index=True)


def prepare(per_store: int = 120, force: bool = False) -> dict[str, pd.DataFrame]:
    out = processed_dir()
    paths = {k: out / f"{k}.parquet" for k in ("panel", "weather", "traffic", "quarantine")}
    if not force and all(p.exists() for p in paths.values()):
        frames = {k: pd.read_parquet(p) for k, p in paths.items()}
        if not frames["panel"]["series_id"].astype(str).str.startswith(synthetic.SYNTHETIC_PREFIX).any():
            frames["panel"] = synthetic.ensure(frames["panel"])  # add the synthetic Dairy/Eggs series once
            frames["panel"].to_parquet(paths["panel"], index=False)
        return frames

    rohlik.download()
    sales = rohlik.load_sales()
    sales = sales[(sales["date"] >= HISTORY_START) & (sales["date"] <= DATA_END)]
    traffic = rohlik.traffic_from_sales(sales)

    keys = select_series(sales, per_store)
    sales = sales[(sales["store_id"] + "|" + sales["product_id"]).isin(set(keys))]
    clean, quarantine = validate_observations(sales)
    panel = synthetic.ensure(complete_daily_index(clean))
    weather = store_weather(HISTORY_START.date(), DATA_END.date())

    frames = {"panel": panel, "weather": weather, "traffic": traffic, "quarantine": quarantine}
    for k, df in frames.items():
        df.to_parquet(paths[k], index=False)
    return frames
