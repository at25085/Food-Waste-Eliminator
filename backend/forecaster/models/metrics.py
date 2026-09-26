"""Forecast error metrics. Error sign convention: predicted − actual (positive = over-forecast,
which is what creates waste)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def summarize(pred: np.ndarray | pd.Series, actual: np.ndarray | pd.Series) -> dict:
    p = np.asarray(pred, dtype="float64")
    a = np.asarray(actual, dtype="float64")
    ok = ~(np.isnan(p) | np.isnan(a))
    p, a = p[ok], a[ok]
    if len(a) == 0 or a.sum() == 0:
        return {"n": int(len(a)), "mae": None, "rmse": None, "wape": None, "bias": None}
    e = p - a
    return {
        "n": int(len(a)),
        "mae": float(np.abs(e).mean()),
        "rmse": float(np.sqrt((e ** 2).mean())),
        "wape": float(np.abs(e).sum() / a.sum()),
        "bias": float(e.sum() / a.sum()),
    }


def by_segment(df: pd.DataFrame, pred_col: str, actual_col: str, by: str | list[str]) -> pd.DataFrame:
    rows = []
    for key, g in df.groupby(by, observed=True):
        m = summarize(g[pred_col], g[actual_col])
        m.update(dict(zip([by] if isinstance(by, str) else by, key if isinstance(key, tuple) else (key,))))
        rows.append(m)
    return pd.DataFrame(rows)
