"""Assemble the full feature table (stage-1 traffic forecasts + stage-2 features)."""
from __future__ import annotations

import pandas as pd

from forecaster.features.build import build_features
from forecaster.config import settings
from forecaster.models import traffic as stage1

CAL_COLS = ["holiday", "school_holidays", "winter_school_holidays", "shops_closed"]


def store_calendar(panel: pd.DataFrame) -> pd.DataFrame:
    return panel.groupby(["store_id", "date"], as_index=False)[CAL_COLS].max()


def feature_table(frames: dict[str, pd.DataFrame], horizon: int = 1) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (demand feature table, stage-1 store-day frame).

    expected_customer_count is out-of-fold by calendar month: rows in month M come from a
    stage-1 model trained only on months before M, so it is leakage-free for any cutoff.
    """
    panel, weather, traffic = frames["panel"], frames["weather"], frames["traffic"]
    stores = sorted(panel["store_id"].unique())
    s1 = stage1.store_day_frame(traffic, store_calendar(panel), weather, horizon, stores)
    oof = None
    if settings.use_traffic_forecast:  # off by default: measured not to help (architecture_study.json)
        oof = stage1.out_of_fold(s1, cutoff=s1["date"].max())
        s1 = s1.merge(oof, on=["store_id", "date"], how="left")
    feat = build_features(panel, weather, traffic, horizon=horizon, expected_traffic=oof)
    return feat, s1
