"""Stage 1: store-level customer-traffic forecast (expected_customer_count).

Training rows for stage 2 get *out-of-fold, time-respecting* stage-1 predictions: each month is
predicted by a model trained only on earlier months (expanding window), so stage 2 never learns
from a traffic value it could not have had at prediction time.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import xgboost as xgb

from forecaster.features.build import add_holiday_features, traffic_features
from forecaster.features.calendar import HOLIDAY_FEATURES
from forecaster.weather.open_meteo import WEATHER_FEATURES

FEATURES = ["dow", "month", "day_of_year", "is_weekend", "holiday", "school_holidays",
            "winter_school_holidays", "shops_closed", "store_code", *WEATHER_FEATURES, *HOLIDAY_FEATURES,
            "customer_count_lag_h", "customer_count_lag_7", "customer_count_lag_14",
            "customer_count_rolling_7_mean", "customer_count_rolling_28_mean",
            "same_weekday_customer_mean"]
PARAMS = {"objective": "reg:squarederror", "eta": 0.05, "max_depth": 5, "subsample": 0.9,
          "colsample_bytree": 0.9, "min_child_weight": 5, "lambda": 2.0, "tree_method": "hist"}
ROUNDS = 400
MIN_TRAIN_DAYS = 120


def store_day_frame(traffic: pd.DataFrame, calendar: pd.DataFrame, weather: pd.DataFrame,
                    horizon: int, stores: list[str]) -> pd.DataFrame:
    """traffic: store_id,date,customer_count · calendar: store_id,date + holiday flags."""
    tf = traffic_features(traffic, horizon)
    df = tf.merge(calendar, on=["store_id", "date"], how="left") \
           .merge(weather[["store_id", "date", *WEATHER_FEATURES]], on=["store_id", "date"], how="left")
    d = df["date"]
    df["dow"], df["month"], df["day_of_year"] = d.dt.dayofweek, d.dt.month, d.dt.dayofyear
    df["is_weekend"] = (df["dow"] >= 5).astype("int64")
    for c in ["holiday", "school_holidays", "winter_school_holidays", "shops_closed"]:
        df[c] = df[c].fillna(0.0) if c in df else 0.0
    df = add_holiday_features(df)
    df["store_code"] = df["store_id"].map({s: i for i, s in enumerate(sorted(stores))}).astype("float64")
    return df


def _fit(train: pd.DataFrame) -> xgb.Booster:
    t = train.dropna(subset=["customer_count"])
    dtrain = xgb.DMatrix(t[FEATURES].astype("float64"), label=t["customer_count"])
    return xgb.train(PARAMS, dtrain, num_boost_round=ROUNDS)


def fit(frame: pd.DataFrame, cutoff: pd.Timestamp) -> xgb.Booster:
    return _fit(frame[frame["date"] <= cutoff])


def predict(model: xgb.Booster, frame: pd.DataFrame) -> np.ndarray:
    return model.predict(xgb.DMatrix(frame[FEATURES].astype("float64")))


def out_of_fold(frame: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """expected_customer_count for every row ≤ cutoff, each month predicted from earlier months."""
    frame = frame[frame["date"] <= cutoff].copy()
    start = frame["date"].min() + pd.Timedelta(days=MIN_TRAIN_DAYS)
    months = pd.period_range(start, cutoff, freq="M")
    parts = []
    for m in months:
        lo, hi = m.start_time, min(m.end_time.normalize(), cutoff)
        train = frame[frame["date"] < lo]
        block = frame[(frame["date"] >= lo) & (frame["date"] <= hi)]
        if block.empty or train["customer_count"].notna().sum() < 50:
            continue
        model = _fit(train)
        parts.append(block[["store_id", "date"]].assign(expected_customer_count=predict(model, block)))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=["store_id", "date", "expected_customer_count"])
