"""Stage 2: product demand. One global XGBoost model across products and stores."""
from __future__ import annotations

import numpy as np
import pandas as pd
import xgboost as xgb

from forecaster.features.build import CATEGORICAL, TRAFFIC, demand_feature_names, model_matrix

PARAMS = {
    "objective": "reg:tweedie", "tweedie_variance_power": 1.2,
    "eta": 0.05, "max_depth": 8, "min_child_weight": 10, "subsample": 0.8,
    "colsample_bytree": 0.8, "lambda": 5.0, "alpha": 0.5, "tree_method": "hist",
    "max_cat_to_onehot": 1, "max_bin": 256,
}
MAX_ROUNDS = 1500
EARLY_STOPPING = 50
TRAFFIC_DROPOUT = 0.25  # train to work when a store has no traffic feed


def training_rows(feat: pd.DataFrame, exclude_days: pd.DataFrame | None = None,
                  censor_stockouts: bool = False) -> pd.DataFrame:
    """Rows usable as targets: observed sales, store open, and not on an anomaly day whose
    policy is 'exclude' (the day stays in history for lags; it just isn't a training target).
    censor_stockouts: also drop days with availability < 0.9 as TARGETS — recorded sales there are
    a lower bound on demand. They stay in history for lags, and sales are never imputed."""
    rows = feat[feat["sales"].notna() & (feat["shops_closed"] == 0)]
    if censor_stockouts:
        rows = rows[~(rows["availability"] < 0.9)]
    if exclude_days is not None and len(exclude_days):
        key = rows["store_id"].astype(str) + "|" + rows["date"].dt.strftime("%Y-%m-%d")
        bad = set(exclude_days["store_id"].astype(str) + "|" + exclude_days["date"].dt.strftime("%Y-%m-%d"))
        rows = rows[~key.isin(bad)]
    return rows


def categories_of(X: pd.DataFrame) -> dict[str, list[str]]:
    return {c: list(X[c].cat.categories) for c in CATEGORICAL if c in X}


def fit(train: pd.DataFrame, valid: pd.DataFrame, horizon: int = 1, params: dict | None = None,
        seed: int = 7, exclude: tuple[str, ...] = (), weights: np.ndarray | None = None) -> tuple[xgb.Booster, dict]:
    names = [f for f in demand_feature_names(horizon) if f not in exclude]
    params = {**PARAMS, **(params or {}), "seed": seed}
    rng = np.random.default_rng(seed)
    train = train.copy()
    mask = rng.random(len(train)) < TRAFFIC_DROPOUT
    train.loc[mask, [c for c in TRAFFIC if c in names]] = np.nan

    Xtr = model_matrix(train, names)
    cats = categories_of(Xtr)
    Xva = model_matrix(valid, names, cats)
    dtrain = xgb.DMatrix(Xtr, label=train["sales"].to_numpy(), weight=weights, enable_categorical=True)
    dvalid = xgb.DMatrix(Xva, label=valid["sales"].to_numpy(), enable_categorical=True)
    booster = xgb.train(params, dtrain, num_boost_round=MAX_ROUNDS,
                        evals=[(dvalid, "valid")], early_stopping_rounds=EARLY_STOPPING,
                        verbose_eval=False)
    meta = {"feature_names": names, "categories": cats, "params": params,
            "best_iteration": int(booster.best_iteration), "horizon": horizon}
    return booster, meta


def fit_quantile(train: pd.DataFrame, valid: pd.DataFrame, meta: dict, alpha: float = 0.8,
                 seed: int = 7) -> tuple[xgb.Booster, dict]:
    """Upper-quantile model on the same features/categories, used for order sizing."""
    names, cats = meta["feature_names"], meta["categories"]
    params = {**PARAMS, "objective": "reg:quantileerror", "quantile_alpha": alpha, "seed": seed}
    params.pop("tweedie_variance_power", None)
    dtrain = xgb.DMatrix(model_matrix(train, names, cats), label=train["sales"].to_numpy(), enable_categorical=True)
    dvalid = xgb.DMatrix(model_matrix(valid, names, cats), label=valid["sales"].to_numpy(), enable_categorical=True)
    booster = xgb.train(params, dtrain, num_boost_round=MAX_ROUNDS, evals=[(dvalid, "valid")],
                        early_stopping_rounds=EARLY_STOPPING, verbose_eval=False)
    return booster, {**meta, "params": params, "best_iteration": int(booster.best_iteration)}


def refit(full: pd.DataFrame, meta: dict, es_fraction: float, seed: int = 7,
          weights: np.ndarray | None = None) -> tuple[xgb.Booster, dict]:
    """Refit on the whole snapshot (training + early-stopping window) with the tree count found by
    early stopping, scaled for the extra data. Without this, the most recent weeks — the newest
    first-party data — would only ever be used for validation, never learned from."""
    names, cats, params = meta["feature_names"], meta["categories"], meta["params"]
    full = full.copy()
    if params.get("objective") == "reg:tweedie":
        rng = np.random.default_rng(seed)
        mask = rng.random(len(full)) < TRAFFIC_DROPOUT
        full.loc[mask, [c for c in TRAFFIC if c in names]] = np.nan
    rounds = int(round((meta["best_iteration"] + 1) * (1 + es_fraction)))
    d = xgb.DMatrix(model_matrix(full, names, cats), label=full["sales"].to_numpy(), weight=weights,
                    enable_categorical=True)
    booster = xgb.train(params, d, num_boost_round=rounds)
    return booster, {**meta, "best_iteration": rounds - 1, "refit_rounds": rounds,
                     "early_stopping_iteration": meta["best_iteration"]}


def predict(booster: xgb.Booster, meta: dict, df: pd.DataFrame, drop_traffic: bool = False) -> np.ndarray:
    X = model_matrix(df, meta["feature_names"], meta["categories"])
    if drop_traffic:
        X[[c for c in TRAFFIC if c in X]] = np.nan
    d = xgb.DMatrix(X, enable_categorical=True)
    return booster.predict(d, iteration_range=(0, meta["best_iteration"] + 1))


def volatility_weights(df: pd.DataFrame, weight: float = 2.0) -> np.ndarray:
    """Weight 2 for rows whose trailing 28-day sales std is in the top quartile (spike-prone
    series), 1 otherwise. Capped by construction."""
    std = df["sales_roll_28_std"].to_numpy()
    q75 = np.nanquantile(std, 0.75)
    return np.where(np.nan_to_num(std, nan=0.0) >= q75, weight, 1.0)


def baseline_predictions(df: pd.DataFrame, horizon: int = 1) -> dict[str, pd.Series]:
    """Reference forecasts every model must beat."""
    seasonal = df["sales_lag_7"] if horizon <= 7 else df["same_weekday_mean_4w"]
    return {"seasonal_naive_7": seasonal.fillna(df["sales_roll_28_mean"]),
            "rolling_mean_28": df["sales_roll_28_mean"]}
