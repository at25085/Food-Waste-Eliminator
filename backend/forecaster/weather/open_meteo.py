"""Open-Meteo client: historical enrichment + live forecast, normalized to one schema.

Normalized columns (identical for training and serving):
    weather_temperature_max, weather_temperature_min, weather_precipitation_sum,
    weather_code, weather_wind_speed_max
plus metadata column `weather_source` (never a model feature).

Data: Open-Meteo (https://open-meteo.com), CC-BY 4.0.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from forecaster.config import settings

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_FORECAST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

WEATHER_FEATURES = [
    "weather_temperature_max",
    "weather_temperature_min",
    "weather_precipitation_sum",
    "weather_code",
    "weather_wind_speed_max",
]

_DAILY_VARS = {
    "temperature_2m_max": "weather_temperature_max",
    "temperature_2m_min": "weather_temperature_min",
    "precipitation_sum": "weather_precipitation_sum",
    "weather_code": "weather_code",
    "wind_speed_10m_max": "weather_wind_speed_max",
}
# Previous Runs only serves hourly variables with a _previous_dayN suffix; we aggregate to daily.
_HOURLY_PREV = ["temperature_2m", "precipitation", "weather_code", "wind_speed_10m"]


def _cache_dir() -> Path:
    d = settings.data_dir / "cache" / "open_meteo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_json(url: str, params: dict, use_cache: bool = True) -> dict:
    key = hashlib.sha256((url + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:24]
    path = _cache_dir() / f"{key}.json"
    if use_cache and path.exists():
        return json.loads(path.read_text())
    r = httpx.get(url, params=params, timeout=settings.open_meteo_timeout_s)
    r.raise_for_status()
    body = r.json()
    if body.get("error"):
        raise RuntimeError(f"Open-Meteo error: {body.get('reason')}")
    if use_cache:
        path.write_text(json.dumps(body))
    return body


def _daily_frame(body: dict) -> pd.DataFrame:
    daily = body["daily"]
    df = pd.DataFrame({"date": pd.to_datetime(daily["time"])})
    for api_name, norm in _DAILY_VARS.items():
        df[norm] = pd.to_numeric(pd.Series(daily.get(api_name)), errors="coerce")
    return df


def historical_forecast(lat: float, lon: float, start: date, end: date, tz: str) -> pd.DataFrame:
    """Archived forecasts (stitched from the first hours of each run) — training enrichment."""
    body = _get_json(HISTORICAL_FORECAST_URL, {
        "latitude": lat, "longitude": lon, "start_date": str(start), "end_date": str(end),
        "daily": ",".join(_DAILY_VARS), "timezone": tz,
    })
    df = _daily_frame(body)
    df["weather_source"] = "historical_forecast"
    return df


def previous_runs_d1(lat: float, lon: float, start: date, end: date, tz: str) -> pd.DataFrame:
    """What was forecast one day ahead (lead-time-true). Coverage begins in early 2024."""
    body = _get_json(PREVIOUS_RUNS_URL, {
        "latitude": lat, "longitude": lon, "start_date": str(start), "end_date": str(end),
        "hourly": ",".join(f"{v}_previous_day1" for v in _HOURLY_PREV), "timezone": tz,
    })
    h = pd.DataFrame(body["hourly"])
    h["date"] = pd.to_datetime(h.pop("time")).dt.normalize()
    for v in _HOURLY_PREV:
        h[v] = pd.to_numeric(h[f"{v}_previous_day1"], errors="coerce")
    g = h.groupby("date")
    df = pd.DataFrame({
        "weather_temperature_max": g["temperature_2m"].max(),
        "weather_temperature_min": g["temperature_2m"].min(),
        "weather_precipitation_sum": g["precipitation"].sum(min_count=18),
        "weather_code": g["weather_code"].max(),  # WMO codes increase with severity
        "weather_wind_speed_max": g["wind_speed_10m"].max(),
        "_hours": g["temperature_2m"].count(),
    }).reset_index()
    df = df[df["_hours"] >= 18].drop(columns="_hours")  # need a nearly complete day
    df["weather_source"] = "previous_runs_d1"
    return df


def archive_observed(lat: float, lon: float, start: date, end: date, tz: str) -> pd.DataFrame:
    """ERA5 reanalysis — observed-like weather. Used only to cross-check the dataset's weather."""
    body = _get_json(ARCHIVE_URL, {
        "latitude": lat, "longitude": lon, "start_date": str(start), "end_date": str(end),
        "daily": "precipitation_sum,snowfall_sum,temperature_2m_max", "timezone": tz,
    })
    d = body["daily"]
    return pd.DataFrame({
        "date": pd.to_datetime(d["time"]),
        "api_precipitation": pd.to_numeric(pd.Series(d["precipitation_sum"]), errors="coerce"),
        "api_snowfall": pd.to_numeric(pd.Series(d["snowfall_sum"]), errors="coerce"),
        "api_temperature_max": pd.to_numeric(pd.Series(d["temperature_2m_max"]), errors="coerce"),
    })


def live_forecast(lat: float, lon: float, tz: str, days: int = 7) -> pd.DataFrame:
    """The forecast available right now — used for every live prediction. Never cached."""
    body = _get_json(FORECAST_URL, {
        "latitude": lat, "longitude": lon, "forecast_days": days, "timezone": tz,
        "daily": ",".join([*_DAILY_VARS, "precipitation_probability_max"]),
    }, use_cache=False)
    df = _daily_frame(body)
    df["precipitation_probability_max"] = pd.to_numeric(
        pd.Series(body["daily"].get("precipitation_probability_max")), errors="coerce")
    df["weather_source"] = "live_forecast"
    return df


def training_weather(lat: float, lon: float, start: date, end: date, tz: str) -> pd.DataFrame:
    """Best available pre-event weather per day: Previous Runs D+1 where it exists, else the
    archived Historical Forecast. `weather_source` records which one fed each day."""
    hist = historical_forecast(lat, lon, start, end, tz)
    prev_start = max(start, date(2024, 1, 1))
    if prev_start <= end:
        prev = previous_runs_d1(lat, lon, prev_start, end, tz)
        prev = prev.dropna(subset=["weather_temperature_max"])
        hist = pd.concat([hist[~hist["date"].isin(prev["date"])], prev], ignore_index=True)
    return hist.sort_values("date").reset_index(drop=True)


def weather_code_group(code: pd.Series) -> pd.Series:
    """Collapse WMO codes into ordinal buckets: 0 clear … 5 thunderstorm."""
    bins = [-1, 1, 3, 48, 67, 86, 99]
    return pd.cut(code, bins=bins, labels=False).astype("float64")


def cross_check(dataset_weather: pd.DataFrame, api_weather: pd.DataFrame) -> dict:
    """Compare the dataset's own weather against Open-Meteo reanalysis. Reported, not merged."""
    m = dataset_weather.merge(api_weather, on="date", how="inner")
    out: dict = {"days_compared": int(len(m))}
    if len(m):
        both = m.dropna(subset=["dataset_precipitation", "api_precipitation"])
        out["precipitation_corr"] = float(np.corrcoef(both["dataset_precipitation"],
                                                      both["api_precipitation"])[0, 1]) if len(both) > 2 else None
        out["precipitation_mean_abs_diff_mm"] = float((both["dataset_precipitation"] - both["api_precipitation"]).abs().mean())
        wet_ds = both["dataset_precipitation"] > 0.5
        wet_api = both["api_precipitation"] > 0.5
        out["wet_day_agreement"] = float((wet_ds == wet_api).mean())
    return out
