"""Holiday calendar per store and holiday-proximity features.

Holidays are known in advance, so these features describe the forecast date without leakage.
The calendar is the union of the dataset's own holiday flags and the public-holiday calendar of
the store's country/state (python `holidays`), which also covers dates beyond the data — the
dates we actually serve.
"""
from __future__ import annotations

from datetime import date

import holidays
import numpy as np
import pandas as pd
from dateutil.easter import easter

from forecaster.config import STORE_LOCATIONS

HOLIDAY_FEATURES = ["days_to_next_holiday", "days_since_last_holiday", "next_holiday_type",
                    "last_holiday_type", "days_to_next_closure"]
CAP = 21  # beyond three weeks, proximity carries no signal

# 0 = none nearby · 1 = other public holiday · 2 = Easter · 3 = Christmas / New Year
_NONE, _OTHER, _EASTER, _XMAS = 0, 1, 2, 3


def holiday_type(d: date) -> int:
    e = easter(d.year)
    if (e - pd.Timedelta(days=2)).toordinal() <= d.toordinal() <= (e + pd.Timedelta(days=1)).toordinal():
        return _EASTER
    if (d.month == 12 and d.day >= 24) or (d.month == 1 and d.day == 1):
        return _XMAS
    return _OTHER


def store_holidays(loc: dict | None, years: range) -> set[date]:
    """Public holidays for the store's country / state. Unknown location → no public calendar
    (the dataset's own holiday flags still apply)."""
    if not loc or not loc.get("country"):
        return set()
    return set(holidays.country_holidays(loc["country"], subdiv=loc.get("subdiv"), years=list(years)).keys())


def calendar_frame(panel_flags: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp,
                   locations: dict[str, dict] | None = None) -> pd.DataFrame:
    """store_id, date, is_holiday, is_closed for every store-day in [start, end].
    panel_flags: store_id, date, holiday, shops_closed (dataset flags; may stop before `end`).
    locations: store_id → {country, subdiv, …}; defaults to the built-in stores."""
    years = range(start.year - 1, end.year + 2)
    locations = locations or STORE_LOCATIONS
    store_ids = sorted(set(panel_flags["store_id"].astype(str)) | (set(STORE_LOCATIONS) if panel_flags.empty else set()))
    frames = []
    for store_id in store_ids:
        days = pd.DataFrame({"date": pd.date_range(start - pd.Timedelta(days=CAP + 1), end + pd.Timedelta(days=CAP + 1))})
        public = store_holidays(locations.get(store_id), years)
        flags = panel_flags[panel_flags["store_id"] == store_id][["date", "holiday", "shops_closed"]]
        days = days.merge(flags, on="date", how="left").fillna({"holiday": 0.0, "shops_closed": 0.0})
        days["is_holiday"] = (days["holiday"] > 0) | days["date"].dt.date.isin(public)
        days["is_closed"] = days["shops_closed"] > 0
        days["store_id"] = store_id
        frames.append(days[["store_id", "date", "is_holiday", "is_closed"]])
    return pd.concat(frames, ignore_index=True)


def _proximity(dates: np.ndarray, events: np.ndarray, forward: bool) -> np.ndarray:
    if len(events) == 0:
        return np.full(len(dates), CAP, dtype="float64")
    if forward:  # days until the next event on or after the date
        idx = np.searchsorted(events, dates, side="left")
        ok = idx < len(events)
        out = np.full(len(dates), CAP, dtype="float64")
        out[ok] = (events[idx[ok]] - dates[ok]).astype("timedelta64[D]").astype("float64")
    else:  # days since the last event strictly before the date
        idx = np.searchsorted(events, dates, side="left") - 1
        ok = idx >= 0
        out = np.full(len(dates), CAP, dtype="float64")
        out[ok] = (dates[ok] - events[idx[ok]]).astype("timedelta64[D]").astype("float64")
    return np.minimum(out, CAP)


def holiday_features(cal: pd.DataFrame) -> pd.DataFrame:
    """Per store-day proximity to holidays and store closures, plus the kind of holiday."""
    out = []
    for store_id, g in cal.groupby("store_id", sort=False):
        g = g.sort_values("date").copy()
        d = g["date"].to_numpy("datetime64[D]")
        hol = np.sort(g.loc[g["is_holiday"], "date"].to_numpy("datetime64[D]"))
        clo = np.sort(g.loc[g["is_closed"], "date"].to_numpy("datetime64[D]"))
        g["days_to_next_holiday"] = _proximity(d, hol, forward=True)
        g["days_since_last_holiday"] = _proximity(d, hol, forward=False)
        g["days_to_next_closure"] = _proximity(d, clo, forward=True)
        types = {pd.Timestamp(h).date(): holiday_type(pd.Timestamp(h).date()) for h in hol}
        nxt_idx = np.searchsorted(hol, d, side="left")
        prv_idx = np.searchsorted(hol, d, side="left") - 1
        g["next_holiday_type"] = [types[pd.Timestamp(hol[i]).date()] if i < len(hol) and dist < CAP else _NONE
                                  for i, dist in zip(nxt_idx, g["days_to_next_holiday"])]
        g["last_holiday_type"] = [types[pd.Timestamp(hol[i]).date()] if i >= 0 and dist < CAP else _NONE
                                  for i, dist in zip(prv_idx, g["days_since_last_holiday"])]
        out.append(g[["store_id", "date", "is_holiday", *HOLIDAY_FEATURES]])
    return pd.concat(out, ignore_index=True)
