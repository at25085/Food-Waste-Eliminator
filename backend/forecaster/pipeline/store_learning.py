"""Store-specific learning from owner uploads.

    uploads (first-party observations)
      → features for that store (same single feature path as everything else)
      → grade the serving model on uploaded days it never trained on (retroactive evaluation)
      → forecast the day after the last upload (a real, live prediction in the ledger)
      → once ≥ min_new_days of new data exist: train a challenger on global history + this
        store's data (with a per-store calibration learned from its own errors), evaluate on the
        store's most recent unseen days, promote only under the usual rules.

There is ONE model. A store's uploads train a challenger of the global model; it is judged on the
uploading store's recent unseen days AND on the existing stores' same days, and replaces the global
champion only if it is better for the store and no worse for everyone else. Every store is then
re-planned with the new champion.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import insert, select, update

from forecaster.config import settings
from forecaster.data.prepare import processed_dir
from forecaster.db import schema as S
from forecaster.decisions.policy import Shelf
from forecaster.features import build as fb
from forecaster.features.build import FEATURE_SCHEMA_VERSION, build_features, complete_daily_index
from forecaster.models import demand, registry
from forecaster.models.metrics import summarize
from forecaster.pipeline import lifecycle
from forecaster.stores import all_locations, location
from forecaster.weather import open_meteo as om

EVAL_DAYS = 14
TRAIN_START = pd.Timestamp("2022-01-01")


def _now():
    return datetime.now(timezone.utc)


def store_context(store_id: str) -> str:
    return f"store:{store_id}"


# ── models ────────────────────────────────────────────────────────────────────────────────────
def load_candidate(version: str) -> lifecycle.Candidate:
    booster, meta, extras = registry.load(version)
    meta = {**meta, "best_iteration": booster.num_boosted_rounds() - 1}
    p80 = extras.get("p80")
    p80_meta = {**meta, "best_iteration": p80.num_boosted_rounds() - 1} if p80 is not None else None
    spec = extras.get("produce")
    spec_meta = {**meta, "best_iteration": spec.num_boosted_rounds() - 1} if spec is not None else None
    return lifecycle.Candidate(booster, meta, p80, p80_meta, pd.Timestamp(meta["training_start"]),
                               pd.Timestamp(meta["training_end"]), specialist=spec, specialist_meta=spec_meta,
                               calibration=meta.get("calibration") or {})


def serving_version(engine, store_id: str | None = None) -> tuple[str, str]:
    """(version, context) of the one global champion that serves every store."""
    with engine.connect() as conn:
        row = conn.execute(select(S.champion_history.c.version).where(S.champion_history.c.context == "production")
                           .order_by(S.champion_history.c.id.desc()).limit(1)).first()
    if row:
        return row[0], "production"
    raise RuntimeError("no champion trained yet — run train_production")


# ── data ──────────────────────────────────────────────────────────────────────────────────────
def store_panel(engine, store_id: str) -> pd.DataFrame:
    with engine.connect() as conn:
        obs = pd.read_sql(select(S.observations).where(S.observations.c.store_id == store_id), conn)
        traffic = pd.read_sql(select(S.traffic_observations.c.timestamp, S.traffic_observations.c.customers_entered)
                              .where(S.traffic_observations.c.store_id == store_id), conn)
    if obs.empty:
        return obs
    obs["date"] = pd.to_datetime(obs["date"])
    panel = pd.DataFrame({
        "store_id": store_id, "product_id": obs["product_id"].astype(str),
        "series_id": store_id + "|" + obs["product_id"].astype(str),
        "name": obs["product_name"].fillna(obs["product_id"]),
        "category": obs["category"].fillna("Fruit and vegetable"),
        "date": obs["date"], "sales": obs["units_sold"], "sell_price_main": obs["price"],
        "availability": np.nan, "units_received": obs["units_received"], "waste_units": obs["waste_units"],
        "stock_end": obs["inventory"], "shelf_life_days": obs["shelf_life_days"], "holiday": 0.0, "shops_closed": 0.0,
        "school_holidays": 0.0, "winter_school_holidays": 0.0,
        "type_0_discount": obs["discount"].fillna(0.0),
        **{f"type_{i}_discount": 0.0 for i in range(1, 7)},
    })
    for c in ("category_l2", "category_l3", "category_l4"):
        panel[c] = panel["category"]
    panel["availability"] = stock_availability(panel)
    if not traffic.empty:
        t = traffic.assign(date=pd.to_datetime(traffic["timestamp"], utc=True).dt.tz_convert(None).dt.normalize())
        panel = panel.merge(t.groupby("date", as_index=False)["customers_entered"].max()
                            .rename(columns={"customers_entered": "customer_count"}), on="date", how="left")
    else:
        panel["customer_count"] = np.nan
    return complete_daily_index(panel)


def stock_availability(panel: pd.DataFrame) -> pd.Series:
    """In-stock signal from the owner's sheets, in the same scale as the Rohlik data's
    availability (share of the day in stock). A day that ended with an empty shelf after selling
    everything on hand was a stockout — sales were capped, so they under-state demand (0.5: we know
    it sold out, not when). Otherwise in stock all day (1.0). Unknown without closing stock (NaN)."""
    p = panel.sort_values(["product_id", "date"])
    prev = p.groupby("product_id")["stock_end"].shift(1)
    on_hand = prev.fillna(0) + p["units_received"].fillna(0)
    sold_out = (p["stock_end"] <= 1e-9) & (p["sales"] >= on_hand - 1e-6) & (on_hand > 0)
    avail = pd.Series(np.where(p["stock_end"].isna(), np.nan, np.where(sold_out, 0.5, 1.0)), index=p.index)
    return avail.reindex(panel.index)


def _weather(loc: dict, store_id: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Pre-event weather for history (archived / previous-run forecasts), live forecast for the
    days that have not happened yet — the same normalized schema either way."""
    today = pd.Timestamp(_now().date())
    parts = []
    hist_end = min(end, today - pd.Timedelta(days=1))
    if start <= hist_end:
        parts.append(om.training_weather(loc["lat"], loc["lon"], start.date(), hist_end.date(), loc["tz"]))
    if end >= today:
        parts.append(om.live_forecast(loc["lat"], loc["lon"], loc["tz"], days=7))
    w = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["date", *om.WEATHER_FEATURES])
    w["store_id"] = store_id
    return w.drop_duplicates(["store_id", "date"], keep="last")


def store_features(engine, store_id: str, with_next_day: bool = True) -> pd.DataFrame:
    panel = store_panel(engine, store_id)
    if panel.empty:
        return panel
    last = panel["date"].max()
    if with_next_day:
        nxt = panel[panel["date"] == last].copy()
        nxt["date"] = last + pd.Timedelta(days=1)
        for c in ["sales", "availability", "customer_count", "units_received", "waste_units", "stock_end"]:
            nxt[c] = np.nan
        for c in [c for c in nxt.columns if c.endswith("_discount")]:
            nxt[c] = 0.0
        nxt["holiday"] = 0.0
        panel = pd.concat([panel, nxt], ignore_index=True)
    loc = location(engine, store_id)
    weather = _weather(loc, store_id, panel["date"].min() - pd.Timedelta(days=30), panel["date"].max()) if loc else None
    traffic = panel.groupby(["store_id", "date"], as_index=False)["customer_count"].max().dropna()
    fb.LOCATIONS = all_locations(engine)
    try:
        feat = build_features(panel, weather, traffic if len(traffic) else None, horizon=1)
    finally:
        fb.LOCATIONS = None
    return feat


def product_shelf_life(g: pd.DataFrame, learned: dict[str, int] | None = None) -> int:
    """Owner-supplied shelf life (latest value) > learned from the batch sheet > category default."""
    owner = g["shelf_life_days"].dropna() if "shelf_life_days" in g else pd.Series(dtype=float)
    if len(owner):
        return max(int(round(owner.iloc[-1])), 1)
    pid = str(g["product_id"].iloc[0])
    if learned and pid in learned:
        return learned[pid]
    return settings.shelf_life_days.get(str(g["category"].iloc[0]), 3)


def batches(engine, store_id: str) -> pd.DataFrame:
    with engine.connect() as conn:
        b = pd.read_sql(select(S.batches).where(S.batches.c.store_id == store_id), conn)
    for c in ("received_date", "expiry_date"):
        b[c] = pd.to_datetime(b[c])
    return b


def learned_shelf_lives(b: pd.DataFrame) -> dict[str, int]:
    """Median sellable days per product from the batch sheet (expiry = last sellable day)."""
    if b.empty:
        return {}
    life = (b["expiry_date"] - b["received_date"]).dt.days + 1
    return {str(k): int(v) for k, v in life.groupby(b["product_id"]).median().round().items()}


def open_batch_cohorts(b: pd.DataFrame, tomorrow: pd.Timestamp) -> dict[str, list[tuple[float, int]]]:
    """Real stock by expiry from batches still on the shelf: (units left, sellable days incl. tomorrow)."""
    if b.empty:
        return {}
    left = b["quantity"] - b["sold"].fillna(0) - b["wasted"].fillna(0)
    days = (b["expiry_date"] - tomorrow).dt.days + 1
    ok = (left > 1e-9) & (days >= 1)
    out: dict[str, list] = {}
    for pid, u, d in zip(b.loc[ok, "product_id"].astype(str), left[ok], days[ok]):
        out.setdefault(pid, []).append((float(u), int(d)))
    return out


def shelf_cohorts(shelf: Shelf) -> list[tuple[float, int]]:
    """FIFO estimate from the daily sheet: bucket i (i days on the shelf tonight) has
    life − i − 1 sellable days left from tomorrow; the last bucket expires tonight."""
    return [(float(u), shelf.life - i - 1) for i, u in enumerate(shelf.b) if u > 1e-9 and shelf.life - i - 1 >= 1]


def sell_through(engine, store_id: str) -> pd.DataFrame:
    """Per product, from the owner's own sheets: of what went on the shelf, how much sold before it
    spoiled. sell_through = sold / (sold + wasted); spoilage_rate = wasted / received."""
    with engine.connect() as conn:
        obs = pd.read_sql(select(S.observations).where(S.observations.c.store_id == store_id), conn)
    if obs.empty:
        return obs
    g = obs.groupby("product_id").agg(
        product_name=("product_name", "last"), category=("category", "last"),
        days=("date", "nunique"), received=("units_received", "sum"), sold=("units_sold", "sum"),
        wasted=("waste_units", "sum"), shelf_life_days=("shelf_life_days", "last")).reset_index()
    g["sell_through_before_spoilage"] = g["sold"] / (g["sold"] + g["wasted"]).where(lambda x: x > 0)
    g["spoilage_rate"] = g["wasted"] / g["received"].where(lambda x: x > 0)
    g["source"] = "daily sheet (first-in-first-out estimate)"
    b = batches(engine, store_id)
    closed = b[b["sold"].notna() & b["wasted"].notna()] if len(b) else b
    if len(closed):
        exact = closed.groupby("product_id").agg(batches=("batch_id", "nunique"), b_qty=("quantity", "sum"),
                                                 b_sold=("sold", "sum"), b_wasted=("wasted", "sum")).reset_index()
        g = g.merge(exact, on="product_id", how="left")
        has = g["batches"].notna()
        g.loc[has, "sell_through_before_spoilage"] = g.loc[has, "b_sold"] / g.loc[has, "b_qty"]
        g.loc[has, "spoilage_rate"] = g.loc[has, "b_wasted"] / g.loc[has, "b_qty"]
        g.loc[has, "source"] = "batch sheet (exact per delivery)"
        g = g.drop(columns=["b_qty", "b_sold", "b_wasted"])
    return g.sort_values("wasted", ascending=False)


# ── grading and forecasting ───────────────────────────────────────────────────────────────────
def evaluate_uploaded_days(engine, store_id: str, upload_id: str | None = None) -> dict:
    """Retroactive evaluation: what the serving model would have predicted for each uploaded day
    it never trained on, using only data before that day. Written to the ledger under context
    'store_eval' (clearly not live predictions) with the real outcome attached."""
    version, _ = serving_version(engine, store_id)
    cand = load_candidate(version)
    feat = store_features(engine, store_id, with_next_day=False)
    if feat.empty:
        return {"evaluated_rows": 0}
    rows = feat[feat["sales"].notna() & (feat["date"] > cand.training_end)]
    with engine.connect() as conn:
        done = pd.read_sql(select(S.predictions.c.product_id, S.predictions.c.forecast_date).where(
            (S.predictions.c.context == "store_eval") & (S.predictions.c.store_id == store_id)), conn)
    if len(done):
        key = set(zip(done["product_id"], pd.to_datetime(done["forecast_date"])))
        rows = rows[[(p, d) not in key for p, d in zip(rows["product_id"].astype(str), rows["date"])]]
    # need some history for lag features; the first days of a brand-new store have none
    rows = rows[rows["sales_lag_7"].notna()]
    if rows.empty:
        return {"evaluated_rows": 0, "version": version}
    p50 = cand.predict(rows)
    now = _now()
    preds, outs = [], []
    for (_, r), p in zip(rows.iterrows(), p50):
        pid = uuid.uuid4().hex
        preds.append(dict(prediction_id=pid, context="store_eval", store_id=store_id, product_id=str(r["product_id"]),
                          category=str(r["category"]), prediction_created_at=now, forecast_date=r["date"].date(),
                          horizon=1, predicted_units=float(p), p80_units=None, weather_forecast=None,
                          expected_customers=None, model_version=version, feature_schema_version=FEATURE_SCHEMA_VERSION))
        outs.append(dict(prediction_id=pid, actual_units_sold=float(r["sales"]), actual_customer_count=None,
                         actual_inventory_remaining=None, waste_units=None, observed_at=now))
    promo = rows["discount_max"].fillna(0) > 0
    experiments = []
    if promo.any():
        cf = rows[promo].copy()
        cf[[c for c in cf.columns if c.startswith("type_") and c.endswith("_discount")]] = 0.0
        cf["discount_max"], cf["any_discount"], cf["n_discount_types"] = 0.0, 0, 0
        base = cand.predict(cf)
        for (_, r), b0 in zip(rows[promo].iterrows(), base):
            experiments.append(dict(
                experiment_id=uuid.uuid4().hex[:12], store_id=store_id, product_id=str(r["product_id"]),
                discount=float(r["discount_max"]), start_time=r["date"].to_pydatetime().replace(tzinfo=timezone.utc),
                end_time=(r["date"] + pd.Timedelta(days=1)).to_pydatetime().replace(tzinfo=timezone.utc),
                inventory_before=None, forecast_without_promotion=float(b0), actual_sales=float(r["sales"]),
                inventory_after=None if pd.isna(r.get("stock_end")) else float(r["stock_end"]),
                waste_after=None if pd.isna(r.get("waste_units")) else float(r["waste_units"]),
                revenue=None if pd.isna(r["sell_price_main"]) else float(r["sales"] * r["sell_price_main"] * (1 - r["discount_max"]))))
    with engine.begin() as conn:
        conn.execute(insert(S.predictions), preds)
        conn.execute(insert(S.outcomes), outs)
        if experiments:
            conn.execute(insert(S.promotion_experiments), experiments)
        if upload_id:
            conn.execute(update(S.uploads).where(S.uploads.c.upload_id == upload_id)
                         .values(evaluated_days=int(rows["date"].nunique())))
    return {"evaluated_rows": int(len(rows)), "evaluated_days": int(rows["date"].nunique()), "version": version,
            **{k: v for k, v in summarize(p50, rows["sales"]).items() if k in ("wape", "bias")}}


def forecast_next_day(engine, store_id: str) -> pd.DataFrame:
    """Live forecast for the day after the last upload, with real stock from the owner's sheets.
    Writes the predictions to the ledger (outcomes arrive with the next upload)."""
    version, ctx = serving_version(engine, store_id)
    cand = load_candidate(version)
    feat = store_features(engine, store_id, with_next_day=True)
    if feat.empty:
        return feat
    tomorrow = feat["date"].max()
    nf = feat[feat["date"] == tomorrow].copy()
    nf["p50"] = cand.predict(nf)
    nf["p80"] = cand.predict_p80(nf, nf["p50"].to_numpy())
    for d in (0.2, 0.3, 0.35, 0.45, 0.5, 0.6):
        cf = nf.copy()
        cf["discount_max"], cf["any_discount"], cf["n_discount_types"] = d, 1, 1
        nf[f"p50_if_markdown_{int(d * 100)}"] = cand.predict(cf)

    # Stock entering tomorrow from the owner's REAL sheets: replay what they received and sold
    # through a FIFO shelf to know how old the remaining units are.
    hist = feat[feat["date"] < tomorrow].sort_values("date")
    b = batches(engine, store_id)
    learned = learned_shelf_lives(b)
    real = open_batch_cohorts(b, tomorrow)
    inv = []
    for pid, g in hist.groupby("product_id", observed=True):
        life = product_shelf_life(g, learned)
        if str(pid) in real:  # the batch sheet says exactly what is on the shelf and when it expires
            cohorts, source = real[str(pid)], "batch sheet"
        else:
            shelf = Shelf(life)
            for rec, sold, wasted in zip(g["units_received"].fillna(0), g["sales"].fillna(0), g["waste_units"].fillna(0)):
                shelf.step(rec, sold + wasted)
            cohorts, source = shelf_cohorts(shelf), "daily sheet (FIFO estimate)"
        inv.append({"product_id": str(pid), "on_hand": sum(u for u, _ in cohorts),
                    "expiring_tomorrow": sum(u for u, d in cohorts if d == 1), "shelf_life": life,
                    "cohorts": json.dumps(cohorts), "stock_source": source})
    rec = nf[["store_id", "product_id", "name", "category", "sell_price_main", "p50", "p80",
              *[c for c in nf.columns if c.startswith("p50_if_markdown_")], "customer_count_rolling_7_mean",
              *om.WEATHER_FEATURES]].copy()
    for c in ["store_id", "product_id", "category"]:
        rec[c] = rec[c].astype(str)
    rec = rec.merge(pd.DataFrame(inv), on="product_id", how="left").fillna({"on_hand": 0.0, "expiring_tomorrow": 0.0})
    rec["forecast_date"] = str(tomorrow.date())
    rec["as_of"] = str((tomorrow - pd.Timedelta(days=1)).date())
    rec["model_version"] = version
    rec["inventory_source"] = "owner uploads (real)"
    from forecaster.decisions.policy import critical_ratio, order_quantity
    rec["recommended_order"] = order_quantity(rec["p50"], rec["p80"], critical_ratio(settings.default_waste_cost_ratio),
                                              on_hand_fresh=rec["on_hand"])
    out_dir = settings.artifacts_dir / "store_recs"
    out_dir.mkdir(parents=True, exist_ok=True)
    rec.to_parquet(out_dir / f"{store_id}.parquet", index=False)

    now = _now()
    with engine.begin() as conn:
        existing = {r[0] for r in conn.execute(select(S.predictions.c.product_id).where(
            (S.predictions.c.context == "production") & (S.predictions.c.store_id == store_id)
            & (S.predictions.c.forecast_date == tomorrow.date())))}
        rows = [dict(prediction_id=uuid.uuid4().hex, context="production", store_id=store_id,
                     product_id=r.product_id, category=r.category, prediction_created_at=now,
                     forecast_date=tomorrow.date(), horizon=1, predicted_units=float(r.p50), p80_units=float(r.p80),
                     weather_forecast={k: (None if pd.isna(getattr(r, k)) else float(getattr(r, k)))
                                       for k in om.WEATHER_FEATURES},
                     expected_customers=None, model_version=version, feature_schema_version=FEATURE_SCHEMA_VERSION,
                     recommended_order=float(r.recommended_order))
                for r in rec.itertuples() if r.product_id not in existing]
        if rows:
            conn.execute(insert(S.predictions), rows)
    return rec


MIN_EXPERIMENTS = 15


def store_lift(engine, store_id: str) -> dict[str, dict[float, float]]:
    """This store's measured response to discounts, per category and discount level:
    Σ actual / Σ forecast-without-discount − 1 over graded promotion days. Only levels with
    ≥ MIN_EXPERIMENTS observations are returned; others fall back to the model estimate."""
    from forecaster.decisions.markdown_plan import LEVELS
    with engine.connect() as conn:
        e = pd.read_sql(select(S.promotion_experiments).where(S.promotion_experiments.c.store_id == store_id), conn)
        obs = pd.read_sql(select(S.observations.c.product_id, S.observations.c.category)
                          .where(S.observations.c.store_id == store_id).distinct(), conn)
    if e.empty:
        return {}
    e = e.merge(obs.drop_duplicates("product_id"), on="product_id", how="left")
    e["level"] = e["discount"].map(lambda d: min(LEVELS, key=lambda lv: abs(lv - d)))
    out: dict[str, dict[float, float]] = {}
    for (cat, lv), g in e.dropna(subset=["forecast_without_promotion"]).groupby(["category", "level"]):
        if len(g) >= MIN_EXPERIMENTS and g["forecast_without_promotion"].sum() > 0:
            out.setdefault(str(cat), {})[float(lv)] = float(g["actual_sales"].sum() / g["forecast_without_promotion"].sum() - 1)
    return out


def adherence(engine, store_id: str) -> dict:
    """Did the store order what we recommended, and how much waste followed over-ordering?
    Waste attributed to over-ordering = waste in the product's shelf-life window after an
    over-order, capped at the excess ordered."""
    with engine.connect() as conn:
        rec = pd.read_sql(select(S.predictions.c.product_id, S.predictions.c.forecast_date, S.predictions.c.recommended_order)
                          .where((S.predictions.c.store_id == store_id) & (S.predictions.c.context == "production")
                                 & S.predictions.c.recommended_order.isnot(None)), conn)
        obs = pd.read_sql(select(S.observations).where(S.observations.c.store_id == store_id), conn)
    if rec.empty or obs.empty:
        return {"compared_orders": 0}
    rec["forecast_date"], obs["date"] = pd.to_datetime(rec["forecast_date"]), pd.to_datetime(obs["date"])
    m = rec.merge(obs, left_on=["product_id", "forecast_date"], right_on=["product_id", "date"])
    m = m[m["units_received"].notna()]
    if m.empty:
        return {"compared_orders": 0}
    m["excess"] = m["units_received"] - m["recommended_order"]
    tol = np.maximum(1.0, 0.1 * m["recommended_order"])
    followed = (m["excess"].abs() <= tol)
    attributable = 0.0
    obs = obs.sort_values("date")
    for r in m[m["excess"] > tol].itertuples():
        life = int(r.shelf_life_days) if not pd.isna(r.shelf_life_days) else settings.shelf_life_days.get(str(r.category), 3)
        w = obs[(obs["product_id"] == r.product_id) & (obs["date"] > r.date) &
                (obs["date"] <= r.date + pd.Timedelta(days=life))]["waste_units"].fillna(0).sum()
        attributable += min(w, r.excess)
    over = m[m["excess"] > tol].assign(pct=lambda d: d["excess"] / d["recommended_order"].clip(lower=1))
    return {
        "compared_orders": int(len(m)), "followed_share": float(followed.mean()),
        "over_ordered": int((m["excess"] > tol).sum()), "under_ordered": int((m["excess"] < -tol).sum()),
        "excess_units": float(m.loc[m["excess"] > 0, "excess"].sum()),
        "waste_after_over_ordering": float(attributable),
        "worst": over.sort_values("excess", ascending=False)[["product_id", "product_name", "forecast_date",
                                                             "recommended_order", "units_received", "pct"]]
                   .head(5).assign(forecast_date=lambda d: d["forecast_date"].dt.strftime("%Y-%m-%d"))
                   .replace({np.nan: None}).to_dict("records"),
    }


# ── learning status and retraining ────────────────────────────────────────────────────────────
def status(engine, store_id: str) -> dict:
    version, ctx = serving_version(engine, store_id)
    meta = json.loads((registry.models_dir() / version / "meta.json").read_text())
    trained_through = pd.Timestamp(meta["training_end"])
    with engine.connect() as conn:
        days = pd.read_sql(select(S.observations.c.date).where(S.observations.c.store_id == store_id).distinct(), conn)
        up = pd.read_sql(select(S.uploads).where(S.uploads.c.store_id == store_id).order_by(S.uploads.c.created_at), conn)
        q = pd.read_sql(select(S.quarantine.c.reason, S.quarantine.c.table_name)
                        .where(S.quarantine.c.table_name.in_([f"upload:{u}" for u in up["upload_id"]] or ["-"])), conn)
        ev = pd.read_sql(select(S.predictions.c.predicted_units, S.outcomes.c.actual_units_sold)
                         .select_from(S.predictions.join(S.outcomes, S.predictions.c.prediction_id == S.outcomes.c.prediction_id))
                         .where((S.predictions.c.store_id == store_id)
                                & S.predictions.c.context.in_(["store_eval", "production"])), conn)
    days = pd.to_datetime(days["date"]) if len(days) else pd.Series(dtype="datetime64[ns]")
    new_days = int((days > trained_through).sum())
    graded = summarize(ev["predicted_units"], ev["actual_units_sold"]) if len(ev) else None
    return {
        "store_id": store_id, "serving_version": version, "serving_context": ctx,
        "serving_is_store_specific": False,
        "has_learned_from_this_store": pd.Timestamp(meta["training_end"]) >= (days.min() if len(days) else pd.Timestamp.max),
        "uploaded_days": int(len(days)), "first_day": str(days.min().date()) if len(days) else None,
        "last_day": str(days.max().date()) if len(days) else None,
        "new_days_since_model_training": new_days, "retrain_threshold_days": settings.min_new_days_for_retrain,
        "retrain_eligible": new_days >= settings.min_new_days_for_retrain + EVAL_DAYS,
        "uploads": int(len(up)), "quarantined_rows": int(len(q)),
        "quarantine_reasons": q["reason"].str.split(";").explode().value_counts().to_dict() if len(q) else {},
        "graded_forecasts": graded,
        "real_waste_units": float(up["summary"].map(lambda s: (s or {}).get("real_waste_units", 0)).sum()) if len(up) else 0.0,
        "explanation": ("Retraining needs the new days to be split: the challenger learns from the older "
                        f"ones and is judged on the most recent {EVAL_DAYS} it never saw."),
    }


EXISTING_MAX_GLOBAL_DEGRADATION = 0.01  # the existing stores may not get more than 1% worse


def retrain_store(engine, store_id: str, log=print) -> dict:
    """Challenger of the ONE global model = global history + this store's uploads (+ a per-store
    calibration learned from its own errors). Judged on days neither version trained on:
      1. this store's last EVAL_DAYS days — the usual promotion rules (lifecycle.decide);
      2. the existing stores' same days — no more than 1% worse overall, no category > 5% worse.
    Promoted only if both pass; then every store is re-planned with the new champion."""
    from forecaster.pipeline.train_production import plan_existing_stores
    st = status(engine, store_id)
    if not st["retrain_eligible"]:
        return {"decision": "skipped", "reason": f"{st['new_days_since_model_training']} new days; need "
                f"{settings.min_new_days_for_retrain + EVAL_DAYS} ({settings.min_new_days_for_retrain} to learn + {EVAL_DAYS} to judge)"}
    champ_version, _ = serving_version(engine)
    champ = load_candidate(champ_version)
    if champ.meta.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        raise RuntimeError(f"serving model uses schema {champ.meta.get('feature_schema_version')}, code is {FEATURE_SCHEMA_VERSION}")
    sf = store_features(engine, store_id, with_next_day=False)
    last = sf.loc[sf["sales"].notna(), "date"].max()
    cutoff = last - pd.Timedelta(days=EVAL_DAYS)
    glob = pd.read_parquet(processed_dir() / "features_h1.parquet")
    for c in ("store_id", "category", "category_l2", "product_id"):
        glob[c] = glob[c].astype(str)
        sf[c] = sf[c].astype(str)
    rows = demand.training_rows(pd.concat([glob, sf[glob.columns.intersection(sf.columns)]], ignore_index=True))
    for c in ("store_id", "category", "category_l2", "product_id"):
        rows[c] = rows[c].astype("category")
    log(f"training a challenger of the global model: all stores + {store_id}'s uploads through {cutoff.date()}")
    cand = lifecycle.train_candidate(rows, cutoff, TRAIN_START, config={"store_calibration": True})
    in_window = (rows["date"] > cutoff) & (rows["date"] <= last) & rows["sales_lag_7"].notna()
    window = rows[in_window & (rows["store_id"].astype(str) == store_id)]
    others = rows[in_window & (rows["store_id"].astype(str) != store_id)]

    champ_pred, cand_pred = champ.predict(window), cand.predict(window)
    champ_eval, cand_eval = lifecycle.evaluate(champ_pred, window), lifecycle.evaluate(cand_pred, window)
    base_eval = {k: lifecycle.evaluate(v.to_numpy(), window) for k, v in demand.baseline_predictions(window).items()}
    mid = cutoff + pd.Timedelta(days=EVAL_DAYS // 2)
    halves = [(summarize(champ_pred[m], window["sales"][m])["wape"], summarize(cand_pred[m], window["sales"][m])["wape"])
              for m in [(window["date"] <= mid).to_numpy(), (window["date"] > mid).to_numpy()]]
    decision, reason, checks = lifecycle.decide(champ_eval, cand_eval, base_eval, cand.train_metrics, halves)

    existing = {"rows": int(len(others))}
    if len(others):
        oc, on = lifecycle.evaluate(champ.predict(others), others), lifecycle.evaluate(cand.predict(others), others)
        worst = max(((on["by_category"][c]["wape"] - oc["by_category"][c]["wape"]) / oc["by_category"][c]["wape"])
                    for c in lifecycle.MAJOR_CATEGORIES if oc["by_category"][c]["wape"])
        change = (on["global"]["wape"] - oc["global"]["wape"]) / oc["global"]["wape"]
        existing.update(champion=oc, challenger=on, global_change=change, worst_category_change=worst,
                        ok=change <= EXISTING_MAX_GLOBAL_DEGRADATION and worst <= settings.max_category_wape_degradation)
        if decision == "promoted" and not existing["ok"]:
            decision = "rejected"
            reason = (f"better for {store_id} ({reason}) but the existing stores would get worse: "
                      f"{change:+.1%} overall, worst category {worst:+.1%}")
        elif decision == "promoted":
            reason += f"; existing stores {change:+.1%} (worst category {worst:+.1%})"

    version = registry.next_version()
    registry.save(version, cand.booster, {**cand.meta, "context": "production", "trained_with_uploads_from": store_id,
                                          "train_metrics": cand.train_metrics,
                                          "eval_window": [str((cutoff + pd.Timedelta(days=1)).date()), str(last.date())],
                                          "decision": decision, "reason": reason}, lifecycle.extra_models(cand))
    comparison = {"champion": champ_eval, "challenger": cand_eval, "baselines": base_eval, "checks": checks,
                  "existing_stores": existing, "champion_version": champ_version, "uploading_store": store_id}
    with engine.begin() as conn:
        conn.execute(insert(S.model_versions).values(
            version=version, stage="demand", training_start=TRAIN_START.date(), training_end=cutoff.date(),
            feature_schema_version=FEATURE_SCHEMA_VERSION, params=cand.meta["params"],
            metrics={"train": cand.train_metrics, "eval": cand_eval["global"], "calibration": cand.calibration,
                     "existing_stores": {k: v for k, v in existing.items() if k in ("global_change", "worst_category_change", "ok")}},
            artifact_uri=str(registry.models_dir() / version), created_at=_now(),
            promotion_status="champion" if decision == "promoted" else "rejected", decision_reason=reason,
            context="production"))
        conn.execute(insert(S.retrain_runs).values(
            run_id=uuid.uuid4().hex[:12], context=f"global_from:{store_id}", as_of_date=last.date(), trigger="owner_upload",
            champion_version=champ_version, candidate_version=version,
            new_observation_days=st["new_days_since_model_training"], comparison=comparison,
            decision=decision, reason=reason, created_at=_now()))
        if decision == "promoted":
            conn.execute(update(S.model_versions).where(S.model_versions.c.version == champ_version)
                         .values(promotion_status="retired"))
            conn.execute(insert(S.champion_history).values(context="production", version=version, effective_at=_now(),
                                                           as_of_date=last.date(), reason=reason))
    log(f"{version}: {decision} — {reason}")
    if decision == "promoted":
        plan_existing_stores(engine, cand, version, log)  # every store served by the same model
        forecast_next_day(engine, store_id)
    return {"decision": decision, "reason": reason, "candidate": version, "champion": champ_version,
            "champion_wape": champ_eval["global"]["wape"], "challenger_wape": cand_eval["global"]["wape"],
            "existing_stores_change": existing.get("global_change"), "calibration": cand.calibration.get(store_id)}
