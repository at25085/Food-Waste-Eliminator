"""The single feature path used by both training and serving.

Semantics: a prediction is made at the end of day t for forecast_date = t + horizon. Every
history-derived feature is shifted by at least `horizon` days, so a row for forecast_date can
only see data up to t. Calendar, price and planned-discount columns describe the forecast date
itself; they are known in advance.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from forecaster.features.calendar import HOLIDAY_FEATURES, calendar_frame, holiday_features
from forecaster.weather.open_meteo import WEATHER_FEATURES, weather_code_group

FEATURE_SCHEMA_VERSION = "fs_v5"  # v5: seven discount types + 7-day-lagged discount

CATEGORICAL = ["store_id", "category", "category_l2", "product_id"]
CALENDAR = ["dow", "month", "day_of_year", "week_of_year", "is_weekend", "holiday",
            "school_holidays", "winter_school_holidays", "shops_closed", *HOLIDAY_FEATURES]
DISCOUNT_TYPES = [f"type_{i}_discount" for i in range(7)]
PRICE = ["sell_price_main", "price_rel_28d", "discount_max", "any_discount", "n_discount_types",
         *DISCOUNT_TYPES, "discount_max_lag_7"]
WEATHER = [*WEATHER_FEATURES, "weather_code_group", "temp_anomaly_28d"]
TRAFFIC = ["customer_count_lag_h", "customer_count_lag_7", "customer_count_lag_14",
           "customer_count_rolling_7_mean", "customer_count_rolling_28_mean",
           "same_weekday_customer_mean", "units_per_100_customers_28d",
           "category_units_per_100_customers_28d", "expected_customer_count"]
_DEMAND_LAGS = (1, 2, 3, 7, 14, 28)


def demand_feature_names(horizon: int) -> list[str]:
    lags = [f"sales_lag_{k}" for k in _DEMAND_LAGS if k >= horizon]
    hist = ["sales_roll_7_mean", "sales_roll_7_std", "sales_roll_28_mean", "sales_ewm_14",
            "same_weekday_mean_4w", "availability_lag_h", "stockout_rate_28d"]
    return [*CATEGORICAL, *CALENDAR, *PRICE, *WEATHER, *lags, *hist, *TRAFFIC]


def complete_daily_index(panel: pd.DataFrame) -> pd.DataFrame:
    """Reindex each series to a contiguous daily range so shift(k) means k days."""
    static = ["store_id", "product_id", "series_id", "name", "category", "category_l2",
              "category_l3", "category_l4"]
    static = [c for c in static if c in panel.columns]
    parts = []
    for _, g in panel.groupby(["store_id", "product_id"], sort=False):
        full = pd.date_range(g["date"].min(), g["date"].max(), freq="D")
        g2 = g.set_index("date").reindex(full)
        g2.index.name = "date"
        for c in static:
            g2[c] = g[c].iloc[0]
        parts.append(g2.reset_index())
    return pd.concat(parts, ignore_index=True)


def add_holiday_features(df: pd.DataFrame) -> pd.DataFrame:
    """Merge holiday proximity; future rows get their holiday flag from the public calendar."""
    flags = df.groupby(["store_id", "date"], as_index=False, observed=True)[["holiday", "shops_closed"]].max()
    flags["store_id"] = flags["store_id"].astype(str)
    hf = holiday_features(calendar_frame(flags, df["date"].min(), df["date"].max()))
    df = df.drop(columns=[c for c in HOLIDAY_FEATURES if c in df]).assign(store_id=df["store_id"].astype(str))
    df = df.merge(hf, on=["store_id", "date"], how="left")
    df["holiday"] = np.maximum(df["holiday"].fillna(0.0), df.pop("is_holiday").fillna(False).astype("float64"))
    return df


def _calendar(df: pd.DataFrame) -> None:
    d = df["date"]
    df["dow"] = d.dt.dayofweek
    df["month"] = d.dt.month
    df["day_of_year"] = d.dt.dayofyear
    df["week_of_year"] = d.dt.isocalendar().week.astype("int64")
    df["is_weekend"] = (df["dow"] >= 5).astype("int64")
    for c in ["holiday", "school_holidays", "winter_school_holidays", "shops_closed"]:
        if c not in df:
            df[c] = 0.0
        df[c] = df[c].fillna(0.0)


def traffic_features(traffic: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Store-day traffic features. `traffic` has store_id, date, customer_count (observed)."""
    t = traffic.sort_values(["store_id", "date"]).copy()
    out = []
    for _, g in t.groupby("store_id", sort=False):
        full = pd.date_range(g["date"].min(), g["date"].max(), freq="D")
        g = g.set_index("date").reindex(full)
        g.index.name = "date"
        g["store_id"] = g["store_id"].ffill().bfill()
        c = g["customer_count"]
        base = c.shift(horizon)
        g["customer_count_lag_h"] = base
        g["customer_count_lag_7"] = c.shift(7) if horizon <= 7 else np.nan
        g["customer_count_lag_14"] = c.shift(14) if horizon <= 14 else np.nan
        g["customer_count_rolling_7_mean"] = base.rolling(7, min_periods=3).mean()
        g["customer_count_rolling_28_mean"] = base.rolling(28, min_periods=7).mean()
        same_wd = [c.shift(k) for k in (7, 14, 21, 28) if k >= horizon]
        g["same_weekday_customer_mean"] = pd.concat(same_wd, axis=1).mean(axis=1) if same_wd else np.nan
        out.append(g.reset_index())
    return pd.concat(out, ignore_index=True)


def build_features(panel: pd.DataFrame, weather: pd.DataFrame | None, traffic: pd.DataFrame | None,
                   horizon: int = 1, expected_traffic: pd.DataFrame | None = None) -> pd.DataFrame:
    """Return `panel` with model features appended.

    panel:    product-store-day rows on a contiguous daily index (see complete_daily_index);
              rows to be predicted have sales = NaN but carry planned price/discount/calendar.
    weather:  store_id, date, normalized weather columns (+ weather_source metadata).
    traffic:  store_id, date, customer_count — only for stores with a connected traffic feed.
    expected_traffic: store_id, date, expected_customer_count (stage-1 forecast; out-of-fold
              for training rows).
    """
    df = panel.sort_values(["store_id", "product_id", "date"]).reset_index(drop=True).copy()
    _calendar(df)
    df = add_holiday_features(df).sort_values(["store_id", "product_id", "date"]).reset_index(drop=True)

    key = [df["store_id"], df["product_id"]]
    s = df["sales"]
    grp = s.groupby(key, sort=False)
    for k in _DEMAND_LAGS:
        if k >= horizon:
            df[f"sales_lag_{k}"] = grp.shift(k)
    base = grp.shift(horizon)
    bgrp = base.groupby(key, sort=False)
    df["sales_roll_7_mean"] = bgrp.transform(lambda x: x.rolling(7, min_periods=3).mean())
    df["sales_roll_7_std"] = bgrp.transform(lambda x: x.rolling(7, min_periods=3).std())
    df["sales_roll_28_mean"] = bgrp.transform(lambda x: x.rolling(28, min_periods=7).mean())
    df["sales_ewm_14"] = bgrp.transform(lambda x: x.ewm(span=14, min_periods=3).mean())
    same_wd = [grp.shift(k) for k in (7, 14, 21, 28) if k >= horizon]
    df["same_weekday_mean_4w"] = pd.concat(same_wd, axis=1).mean(axis=1)

    avail = df["availability"].groupby(key, sort=False).shift(horizon)
    df["availability_lag_h"] = avail
    df["stockout_rate_28d"] = (avail < 0.9).astype("float64").where(avail.notna()) \
        .groupby(key, sort=False).transform(lambda x: x.rolling(28, min_periods=7).mean())

    # Price & planned promotions describe the forecast date (known in advance).
    price = df["sell_price_main"].groupby(key, sort=False).ffill()
    df["sell_price_main"] = price
    prev_price = price.groupby(key, sort=False).shift(1)
    df["price_rel_28d"] = price / prev_price.groupby(key, sort=False) \
        .transform(lambda x: x.rolling(28, min_periods=7).mean())
    disc_cols = [c for c in df.columns if c.startswith("type_") and c.endswith("_discount")]
    df[disc_cols] = df[disc_cols].fillna(0.0)
    df["discount_max"] = df[disc_cols].max(axis=1)
    df["any_discount"] = (df["discount_max"] > 0).astype("int64")
    df["n_discount_types"] = (df[disc_cols] > 0).sum(axis=1)
    df["discount_max_lag_7"] = df["discount_max"].groupby(key, sort=False).shift(7)  # past promos, known
    # helper (not a feature): trailing 28-day sales volatility, used for sample weighting
    df["sales_roll_28_std"] = bgrp.transform(lambda x: x.rolling(28, min_periods=7).std())

    # Weather (normalized schema; source is metadata only).
    if weather is not None:
        df = df.merge(weather, on=["store_id", "date"], how="left")
    for c in WEATHER_FEATURES:
        if c not in df:
            df[c] = np.nan
    df["weather_code_group"] = weather_code_group(df["weather_code"])
    tmax_hist = df.groupby("store_id")["weather_temperature_max"].transform(
        lambda x: x.shift(horizon).rolling(28, min_periods=7).mean())
    df["temp_anomaly_28d"] = df["weather_temperature_max"] - tmax_hist

    # Traffic (NaN when the store has no traffic feed — XGBoost treats NaN as missing).
    if traffic is not None and len(traffic):
        tf = traffic_features(traffic, horizon)
        df = df.merge(tf.drop(columns=["customer_count"]), on=["store_id", "date"], how="left")
        cust_base = df["customer_count_lag_h"]
        units_base = df.groupby(["store_id", "product_id"], sort=False)["sales"].shift(horizon)
        df["_u"] = units_base
        df["_c"] = cust_base.where(units_base.notna())
        g2 = df.groupby(["store_id", "product_id"], sort=False)
        u28 = g2["_u"].transform(lambda x: x.rolling(28, min_periods=7).sum())
        c28 = g2["_c"].transform(lambda x: x.rolling(28, min_periods=7).sum())
        df["units_per_100_customers_28d"] = 100 * u28 / c28
        cat_day = df.groupby(["store_id", "category", "date"], as_index=False) \
            .agg(_cu=("_u", "sum"), _cc=("customer_count_lag_h", "first"))
        cat_day = cat_day.sort_values(["store_id", "category", "date"])
        cg = cat_day.groupby(["store_id", "category"], sort=False)
        cat_day["category_units_per_100_customers_28d"] = 100 * \
            cg["_cu"].transform(lambda x: x.rolling(28, min_periods=7).sum()) / \
            cg["_cc"].transform(lambda x: x.rolling(28, min_periods=7).sum())
        df = df.merge(cat_day[["store_id", "category", "date", "category_units_per_100_customers_28d"]],
                      on=["store_id", "category", "date"], how="left")
        df = df.drop(columns=["_u", "_c"])
    if expected_traffic is not None and len(expected_traffic):
        df = df.merge(expected_traffic[["store_id", "date", "expected_customer_count"]],
                      on=["store_id", "date"], how="left")
    for c in TRAFFIC:
        if c not in df:
            df[c] = np.nan

    for c in CATEGORICAL:
        df[c] = df[c].astype("category")
    return df


def model_matrix(df: pd.DataFrame, feature_names: list[str], categories: dict | None = None) -> pd.DataFrame:
    """Select features and pin categorical levels to those seen at training time."""
    X = df[feature_names].copy()
    for c in CATEGORICAL:
        if c in X:
            if categories and c in categories:
                X[c] = pd.Categorical(X[c].astype(str), categories=categories[c])
            else:
                X[c] = X[c].astype(str).astype("category")
    for c in X.columns:
        if c not in CATEGORICAL:
            X[c] = pd.to_numeric(X[c], errors="coerce").astype("float64")
    return X
