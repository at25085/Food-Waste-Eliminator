"""HTTP API for the dashboard."""
from __future__ import annotations

import io
import json
import uuid
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, insert, select, update

from forecaster import briefing, memory
from forecaster.data import uploads
from forecaster.pipeline import store_learning
from forecaster.config import STORE_LOCATIONS, settings
from forecaster.data.validation import validate_traffic
from forecaster.db import schema as S
from forecaster.db.schema import create_all, get_engine
from forecaster.features.build import FEATURE_SCHEMA_VERSION
from forecaster.decisions import impact, markdown_plan
from forecaster.decisions.policy import critical_ratio, order_quantity
from forecaster.models.metrics import summarize
from forecaster.seed import seed_stores
from forecaster.weather import open_meteo as om

app = FastAPI(title="forecaster")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
engine = get_engine()
create_all(engine)
seed_stores(engine)


def _read_json(name: str) -> dict:
    p = settings.artifacts_dir / name
    if not p.exists():
        raise HTTPException(404, f"{name} not generated yet — run the pipeline")
    return json.loads(p.read_text())


_LEDGER_CACHE: dict[str, tuple[tuple, pd.DataFrame]] = {}


def _ledger(context: str, store: str | None = None) -> pd.DataFrame:
    """Predictions joined with outcomes. Cached per context and invalidated whenever the number of
    predictions or outcomes changes (both tables are append-only, so counts are a valid version)."""
    from sqlalchemy import func
    with engine.connect() as conn:
        stamp = (conn.execute(select(func.count()).select_from(S.predictions).where(S.predictions.c.context == context)).scalar(),
                 conn.execute(select(func.count()).select_from(S.outcomes)).scalar())
    hit = _LEDGER_CACHE.get(context)
    if hit is None or hit[0] != stamp:
        _LEDGER_CACHE[context] = (stamp, _load_ledger(context))
    df = _LEDGER_CACHE[context][1]
    return (df[df["store_id"] == store] if store else df).copy()


def _load_ledger(context: str) -> pd.DataFrame:
    q = select(S.predictions, S.outcomes.c.actual_units_sold, S.outcomes.c.actual_customer_count,
               S.outcomes.c.waste_units, S.outcomes.c.observed_at) \
        .select_from(S.predictions.outerjoin(S.outcomes, S.predictions.c.prediction_id == S.outcomes.c.prediction_id)) \
        .where(S.predictions.c.context == context)
    with engine.connect() as conn:
        df = pd.read_sql(q, conn)
    df["forecast_date"] = pd.to_datetime(df["forecast_date"])
    return df


def _champion(context: str) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(select(S.champion_history.c.version).where(S.champion_history.c.context == context)
                           .order_by(S.champion_history.c.id.desc()).limit(1)).first()
    return row[0] if row else None


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Mutating endpoints are open in local dev and locked once API_TOKEN is set (deployment)."""
    if settings.api_token and authorization != f"Bearer {settings.api_token}":
        raise HTTPException(401, "missing or invalid bearer token")


WRITE = [Depends(require_token)]


def _model_lift(r) -> dict[float, float]:
    """Model-estimated lift per markdown level for one product (positive estimates only)."""
    out = {}
    for lv in markdown_plan.LEVELS:
        col = f"p50_if_markdown_{int(round(lv * 100))}"
        if col in r and not pd.isna(r[col]) and r["p50"] > 0:
            v = float(r[col]) / float(r["p50"]) - 1
            if v > 0:
                out[lv] = v
    return out


def _require_current_schema(version: str | None) -> None:
    """Refuse to serve a model trained on a different feature list than the code builds."""
    if version is None:
        return
    with engine.connect() as conn:
        v = conn.execute(select(S.model_versions.c.feature_schema_version)
                         .where(S.model_versions.c.version == version)).scalar()
    if v != FEATURE_SCHEMA_VERSION:
        raise HTTPException(409, f"model {version} was trained on feature schema {v}; the serving code "
                                 f"builds {FEATURE_SCHEMA_VERSION}. Retrain before serving.")


@app.get("/api/meta")
def meta():
    return {"app_display_name": settings.app_display_name,
            "attribution": "Weather data by Open-Meteo.com (CC BY 4.0). Sales data: Rohlik Group (Kaggle).",
            "thresholds": {"bias_warning_threshold": settings.bias_warning_threshold,
                           "min_new_days_for_retrain": settings.min_new_days_for_retrain,
                           "min_global_wape_improvement": settings.min_global_wape_improvement,
                           "max_category_wape_degradation": settings.max_category_wape_degradation,
                           "overfit_ratio_warning": settings.overfit_ratio_warning}}


@app.get("/api/stores")
def stores():
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(select(S.stores).order_by(S.stores.c.store_id))]


@app.get("/api/data-sources")
def data_sources(store: str):
    with engine.connect() as conn:
        st = conn.execute(select(S.stores).where(S.stores.c.store_id == store)).first()
        first_party = conn.execute(select(S.traffic_observations.c.timestamp).where(S.traffic_observations.c.store_id == store)).all()
    if not st:
        raise HTTPException(404, "unknown store")
    return [
        {"key": "sales", "label": "Sales history", "connected": True, "detail": "Rohlik daily product sales (real)"},
        {"key": "inventory", "label": "Inventory", "connected": True, "detail": "Simulated from orders + shelf life (no public waste data)"},
        {"key": "prices", "label": "Prices & promotions", "connected": True, "detail": "Sell price + 7 discount types"},
        {"key": "weather", "label": "Open-Meteo weather", "connected": True, "detail": "Archived forecasts for training, live forecast for serving"},
        {"key": "traffic", "label": "Customer traffic", "connected": bool(st.traffic_connected),
         "detail": ("Daily order counts from the store's own data" if st.traffic_connected else
                    "Customer traffic not connected. Adding store traffic helps separate changes in store visits "
                    "from changes in product demand and improves future forecasts after enough observations."),
         "first_party_rows": len(first_party)},
        {"key": "events", "label": "Local events", "connected": False, "detail": "Future integration (e.g. Ticketmaster)"},
    ]


@app.get("/api/model-health")
def model_health(context: str = "production"):
    champ = _champion(context)
    with engine.connect() as conn:
        mv = conn.execute(select(S.model_versions).where(S.model_versions.c.version == champ)).first() if champ else None
        new_obs = conn.execute(select(S.traffic_observations.c.timestamp).where(S.traffic_observations.c.source != "replay")).all()
        days = len({r[0].date() for r in new_obs})
    if not mv:
        raise HTTPException(404, f"no champion for context {context}")
    m = dict(mv._mapping)
    return {
        "context": context, "champion": champ,
        "training_range": [str(m["training_start"]), str(m["training_end"])],
        "trained_on": "Rohlik sales history + Open-Meteo archived/previous-run forecasts",
        "feature_schema_version": m["feature_schema_version"],
        "serving_schema_version": FEATURE_SCHEMA_VERSION,
        "schema_ok": m["feature_schema_version"] == FEATURE_SCHEMA_VERSION,
        "metrics": m["metrics"], "decision_reason": m["decision_reason"],
        "new_first_party_days": days, "retrain_threshold_days": settings.min_new_days_for_retrain,
        "retrain_eligible": days >= settings.min_new_days_for_retrain,
        "principle": "Continuously measures forecast quality and only promotes a new model version when it "
                     "outperforms the current one on unseen data. A new model can be — and often is — rejected.",
    }


@app.get("/api/models")
def models(context: str = "replay"):
    with engine.connect() as conn:
        rows = conn.execute(select(S.model_versions).where(S.model_versions.c.context == context)
                            .order_by(S.model_versions.c.created_at)).all()
    return [dict(r._mapping) for r in rows]


@app.post("/api/models/{version}/rollback", dependencies=WRITE)
def rollback(version: str, context: str = "production"):
    """Re-point the champion to a previous immutable version. Nothing is deleted."""
    with engine.begin() as conn:
        mv = conn.execute(select(S.model_versions).where(S.model_versions.c.version == version)).first()
        if not mv or mv.context != context:
            raise HTTPException(404, "unknown version for this context")
        was_champion = conn.execute(select(S.champion_history.c.id).where(
            (S.champion_history.c.context == context) & (S.champion_history.c.version == version))).first()
        if not was_champion:
            raise HTTPException(409, f"{version} was never champion ({mv.promotion_status}); "
                                     "rollback may only return to a previously promoted version")
        current = _champion(context)
        if current == version:
            return {"champion": version, "changed": False}
        conn.execute(update(S.model_versions).where(S.model_versions.c.version == current).values(promotion_status="retired"))
        conn.execute(update(S.model_versions).where(S.model_versions.c.version == version).values(promotion_status="champion"))
        conn.execute(insert(S.champion_history).values(context=context, version=version,
                                                       effective_at=datetime.now(timezone.utc),
                                                       reason=f"manual rollback from {current}"))
    return {"champion": version, "changed": True, "previous": current}


@app.get("/api/retrain-runs")
def retrain_runs(context: str = "replay"):
    with engine.connect() as conn:
        rows = conn.execute(select(S.retrain_runs).where(S.retrain_runs.c.context == context)
                            .order_by(S.retrain_runs.c.as_of_date)).all()
    return [dict(r._mapping) for r in rows]


@app.get("/api/metrics")
def metrics(context: str = "replay", window: int = 30, by: str = "global", store: str | None = None):
    df = _ledger(context, store).dropna(subset=["actual_units_sold"])
    if df.empty:
        return []
    end = df["forecast_date"].max()
    df = df[df["forecast_date"] > end - pd.Timedelta(days=window)]
    if by == "global":
        return [{"segment": "all", **summarize(df["predicted_units"], df["actual_units_sold"])}]
    key = {"category": "category", "store": "store_id", "product": "product_id", "model": "model_version"}.get(by)
    if by == "weekday":
        df = df.assign(weekday=df["forecast_date"].dt.day_name())
        key = "weekday"
    if not key:
        raise HTTPException(400, "by must be global|category|store|product|weekday|model")
    out = []
    for seg, g in df.groupby(key):
        out.append({"segment": seg, **summarize(g["predicted_units"], g["actual_units_sold"])})
    return sorted(out, key=lambda r: -(r["n"] or 0))[:200]


@app.get("/api/metrics/timeseries")
def metrics_timeseries(context: str = "replay", store: str | None = None):
    """Daily WAPE / bias, rolling 7/30-day WAPE, the version serving each day and drift flags."""
    df = _ledger(context, store).dropna(subset=["actual_units_sold"])
    if df.empty:
        return {"days": [], "reference_wape": None}
    df["abs_err"] = (df["predicted_units"] - df["actual_units_sold"]).abs()
    df["err"] = df["predicted_units"] - df["actual_units_sold"]
    d = df.groupby("forecast_date").agg(abs_err=("abs_err", "sum"), err=("err", "sum"),
                                        actual=("actual_units_sold", "sum"),
                                        version=("model_version", "first")).reset_index()
    d["wape"] = d["abs_err"] / d["actual"]
    d["bias"] = d["err"] / d["actual"]
    for w in (7, 30):
        d[f"wape_{w}d"] = d["abs_err"].rolling(w, min_periods=3).sum() / d["actual"].rolling(w, min_periods=3).sum()
    ref = float(d["wape"].head(28).mean())
    d["drift"] = (d["wape_30d"] > ref * settings.drift_wape_multiplier)
    d["forecast_date"] = d["forecast_date"].dt.strftime("%Y-%m-%d")
    return {"days": d.replace({np.nan: None}).to_dict("records"), "reference_wape": ref,
            "drift_multiplier": settings.drift_wape_multiplier}


@app.get("/api/ledger")
def ledger(context: str = "replay", store: str | None = None, product: str | None = None, limit: int = 200):
    df = _ledger(context, store)
    if product:
        df = df[df["product_id"] == product]
    df = df.sort_values("forecast_date", ascending=False).head(limit)
    df["forecast_date"] = df["forecast_date"].dt.strftime("%Y-%m-%d")
    df["error"] = df["predicted_units"] - df["actual_units_sold"]
    return df.replace({np.nan: None}).to_dict("records")


@app.get("/api/stores/{store_id}/recommendations")
def recommendations(store_id: str, waste_cost_ratio: float = Query(default=None, ge=0.05, le=2.0),
                    category: str | None = None):
    own = settings.artifacts_dir / "store_recs" / f"{store_id}.parquet"
    p = own if own.exists() else settings.artifacts_dir / "recommendations.parquet"
    if not p.exists():
        raise HTTPException(404, "recommendations not generated yet — run train_production")
    rec = pd.read_parquet(p)
    _require_current_schema(str(rec["model_version"].iloc[0]) if len(rec) else None)
    rec = rec[rec["store_id"] == store_id]
    real_stock = own.exists()
    if category:
        rec = rec[rec["category"] == category]
    wcr = settings.default_waste_cost_ratio if waste_cost_ratio is None else waste_cost_ratio
    ratio = critical_ratio(wcr)
    # on_hand = units sellable tomorrow (last-day units included: under FIFO they sell first),
    # the same netting rule the waste simulator uses.
    rec = rec.assign(
        order_qty=order_quantity(rec["p50"], rec["p80"], ratio, on_hand_fresh=rec["on_hand"]),
        expiring_leftover=np.maximum(rec["expiring_tomorrow"] - rec["p50"], 0),
    )
    rec["excess_ratio"] = rec["expiring_leftover"] / rec["p50"].clip(lower=1e-6)
    measured = store_learning.store_lift(engine, store_id)
    dates = [str((pd.Timestamp(rec["forecast_date"].iloc[0]) + pd.Timedelta(days=k)).date()) for k in range(14)] if len(rec) else []
    plans = []
    for _, r in rec.iterrows():
        life = int(r["shelf_life"]) if "shelf_life" in r and not pd.isna(r.get("shelf_life")) else \
            settings.shelf_life_days.get(str(r["category"]), 3)
        if "cohorts" in r and isinstance(r.get("cohorts"), str):
            cohorts = [tuple(c) for c in json.loads(r["cohorts"])]
        else:  # older plans only stored totals: last-day units + the rest one day fresher
            cohorts = [(float(r["expiring_tomorrow"]), 1), (max(float(r["on_hand"]) - float(r["expiring_tomorrow"]), 0.0), max(life - 1, 1))]
            cohorts = [c for c in cohorts if c[0] > 1e-9]
        # The trained demand model is the source of truth for how sales respond to a discount
        # (it learns from Rohlik's discount days and from every store's uploaded discount days).
        lift = _model_lift(r)
        source = "trained model" if lift else "conservative default"
        price = float(r["sell_price_main"]) if not pd.isna(r.get("sell_price_main")) and r["sell_price_main"] > 0 else 1.0
        mp = markdown_plan.plan(cohorts, float(r["p50"]), price, lift or None, source)
        plans.append(mp)
    rec["surplus_units"] = [mp.surplus_units for mp in plans]
    rec["markdown"] = [max((st.discount for st in mp.steps if st.day == 0), default=0.0) for mp in plans]
    rec["markdown_schedule"] = [[{"date": dates[min(st.day, len(dates) - 1)], "discount": st.discount, "units": st.units,
                                  "days": st.days} for st in mp.steps] for mp in plans]
    rec["money_kept_vs_no_action"] = [mp.money_plan - mp.money_no_action for mp in plans]
    rec["revenue_given_up_on_full_price_units"] = [mp.revenue_given_up for mp in plans]
    rec["measured_lift_check"] = [
        (measured.get(str(c), {}).get(max((st.discount for st in mp.steps), default=0.0)) if mp.steps else None)
        for c, mp in zip(rec["category"], plans)]
    rec["donate_units"] = [mp.donate_units for mp in plans]
    rec["donate_date"] = [dates[min(max(mp.donate_day, 0), len(dates) - 1)] if mp.donate_day is not None and mp.donate_units > 0 else None
                          for mp in plans]
    rec["lift_source"] = [mp.lift_source for mp in plans]
    rec["schedule_text"] = [markdown_plan.describe(mp, str(n), dates) for mp, n in zip(plans, rec["name"])]
    rec["markdown_lift_estimate"] = [
        (_model_lift(r).get(m) if m > 0 else None) for m, (_, r) in zip(rec["markdown"], rec.iterrows())]
    rec["surplus_action"] = [
        "sell" if mp.surplus_units <= 0 and not mp.steps else
        "donate" if not mp.steps else
        "markdown_then_donate" if mp.donate_units > 0 else
        "markdown" if any(st.day == 0 for st in mp.steps) else "markdown_later"
        for mp in plans]
    rec["waste_risk"] = np.where(rec["surplus_units"] > 0.1 * rec["p50"].clip(lower=1), "high",
                                 np.where(rec["surplus_units"] > 0, "watch", "low"))
    rec["_risk_rank"] = rec["waste_risk"].map({"high": 0, "watch": 1, "low": 2})
    rec = rec.sort_values(["_risk_rank", "surplus_units"], ascending=[True, False]).drop(columns="_risk_rank")
    head = rec.iloc[0] if len(rec) else None
    return {
        "store_id": store_id, "as_of": head["as_of"] if head is not None else None,
        "forecast_date": head["forecast_date"] if head is not None else None,
        "model_version": head["model_version"] if head is not None else None,
        "critical_ratio": ratio, "waste_cost_ratio": wcr,
        "weather": {k: (None if head is None or pd.isna(head[k]) else float(head[k])) for k in om.WEATHER_FEATURES},
        "recent_customers_7d": (None if head is None or "customer_count_rolling_7_mean" not in head
                                or pd.isna(head["customer_count_rolling_7_mean"]) else float(head["customer_count_rolling_7_mean"])),
        "items": rec.replace({np.nan: None}).to_dict("records"),
        "note": ("Stock and waste come from this store's own uploaded sheets; forecasts are model output."
                 if real_stock else
                 "Inventory and waste are simulated (FIFO shelf-life simulator); forecasts are real model output."),
        "inventory_basis": ("Stock entering tomorrow is replayed from the owner's received/sold/wasted sheets, "
                            "using owner-supplied shelf lives where given." if real_stock else
                            "Stock entering tomorrow is simulated from the store's legacy practice "
                            "(same weekday last week + a 90% service-level buffer) — the situation on install day."),
        "real_stock": real_stock,
        "donations": impact.units_to_impact(rec.groupby("category")["donate_units"].sum().to_dict()),
    }


def _briefing(store_id: str) -> dict:
    rec = recommendations(store_id, waste_cost_ratio=None, category=None)
    try:
        health = model_health("production")
    except HTTPException:
        health = None
    df = _ledger("replay", store_id).dropna(subset=["actual_units_sold"])
    recent = None
    if len(df):
        df = df[df["forecast_date"] > df["forecast_date"].max() - pd.Timedelta(days=14)]
        m = summarize(df["predicted_units"], df["actual_units_sold"])
        recent = {"days": 14, "wape": m["wape"], "bias": m["bias"], "source": "backtest ledger"}
    risky = " ".join(str(i["name"]) for i in rec["items"][:5])
    fdate = str(rec["forecast_date"])[:10] if rec.get("forecast_date") else None
    notes, notes_source = (memory.relevant_notes(engine, store_id, fdate, f"events or standing orders affecting {risky}")
                           if fdate else ([], "none"))
    facts = briefing.build_facts(rec, health, recent, notes)
    text, provider = briefing.generate(facts)
    return {"text": text, "provider": provider, "facts": facts, "notes_source": notes_source}


_BRIEFING_CACHE: dict[str, dict] = {}


@app.get("/api/stores/{store_id}/briefing")
def get_briefing(store_id: str, refresh: bool = False):
    if refresh or store_id not in _BRIEFING_CACHE:
        _BRIEFING_CACHE[store_id] = _briefing(store_id)
    return {**_BRIEFING_CACHE[store_id], "voice_available": bool(settings.elevenlabs_api_key)}


@app.get("/api/stores/{store_id}/briefing/audio")
def get_briefing_audio(store_id: str):
    text = get_briefing(store_id)["text"]
    try:
        audio = briefing.speak(text)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"ElevenLabs unavailable: {e}")
    if audio is None:
        raise HTTPException(503, "Voice not configured (set ELEVENLABS_API_KEY in backend/.env)")
    return Response(content=audio, media_type="audio/mpeg")


@app.get("/api/model-cards/{version}")
def model_card(version: str):
    """Full model card. Served from MongoDB Atlas when configured, else built from local state."""
    from forecaster.db import model_cards
    coll = model_cards.collection()
    if coll is not None:
        doc = coll.find_one({"_id": version})
        if doc:
            return {**doc, "source": "mongodb_atlas"}
    for c in model_cards.build_cards(engine, only=version):
        if c["_id"] == version:
            return {**c, "source": "local"}
    raise HTTPException(404, "unknown version")


class NoteIn(BaseModel):
    content: str
    kind: str = "event"  # event | preference | override
    applies_on: date | None = None


@app.post("/api/stores/{store_id}/notes", dependencies=WRITE)
def add_note(store_id: str, note: NoteIn):
    if store_learning.location(engine, store_id) is None and store_id not in STORE_LOCATIONS:
        raise HTTPException(404, "unknown store")
    if note.kind not in ("event", "preference", "override") or not note.content.strip():
        raise HTTPException(400, "kind must be event|preference|override and content non-empty")
    out = memory.add_note(engine, store_id, note.content.strip(), note.kind, note.applies_on)
    _BRIEFING_CACHE.pop(store_id, None)
    return out


@app.get("/api/stores/{store_id}/notes")
def get_notes(store_id: str):
    return {"notes": memory.list_notes(engine, store_id), "backboard": memory.enabled(),
            "policy": "Notes inform the briefing; they never change forecast numbers."}


@app.get("/api/integrations")
def integrations():
    """Which optional integrations are configured (never returns the keys)."""
    return {"llm_provider": briefing.active_provider() or "template (no key)",
            "gemini": bool(settings.gemini_api_key), "gemini_model": settings.gemini_model,
            "elevenlabs": bool(settings.elevenlabs_api_key),
            "mongodb_model_cards": bool(settings.mongodb_uri),
            "backboard_memory": memory.enabled(),
            "database": engine.dialect.name,
            # healthy only if at least one hypertable actually exists (e.g. forecast_errors)
            "timescale": {**S.TIMESCALE_STATUS, "healthy": bool(S.TIMESCALE_STATUS["hypertables"])}}


@app.get("/api/stores/{store_id}/weather/live")
def live_weather(store_id: str):
    loc = store_learning.location(engine, store_id)
    if not loc:
        raise HTTPException(404, "unknown store")
    try:
        df = om.live_forecast(loc["lat"], loc["lon"], loc["tz"], days=7)
    except Exception as e:  # noqa: BLE001 — surface upstream failure to the UI
        raise HTTPException(502, f"Open-Meteo unavailable: {e}")
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    return {"store_id": store_id, "city": loc["city"], "fetched_at": datetime.now(timezone.utc).isoformat(),
            "days": df.replace({np.nan: None}).to_dict("records")}


class StoreIn(BaseModel):
    store_id: str
    city: str
    lat: float
    lon: float
    timezone: str = "America/New_York"
    country: str = "US"
    subdivision: str | None = None
    kind: str = "demo"  # demo (team-authored data) | owner (a real store's data)


def _require_uploadable(store_id: str) -> None:
    with engine.connect() as conn:
        st = conn.execute(select(S.stores).where(S.stores.c.store_id == store_id)).first()
    if not st:
        raise HTTPException(404, "unknown store — create it first")
    if store_id in STORE_LOCATIONS:
        raise HTTPException(409, "the built-in Rohlik warehouses are the model's history; create your own store to upload sheets")


@app.post("/api/stores", dependencies=WRITE)
def create_store(st: StoreIn):
    sid = st.store_id.strip().replace(" ", "_")
    if not sid or len(sid) > 40 or not sid.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(400, "store_id: letters, digits, '_' or '-' (max 40)")
    if st.kind not in ("demo", "owner"):
        raise HTTPException(400, "kind must be demo or owner")
    with engine.begin() as conn:
        if conn.execute(select(S.stores.c.store_id).where(S.stores.c.store_id == sid)).first():
            raise HTTPException(409, "store already exists")
        conn.execute(insert(S.stores).values(
            store_id=sid, city=st.city, lat=st.lat, lon=st.lon, timezone=st.timezone, country=st.country.upper(),
            subdivision=st.subdivision, traffic_connected=False, kind=st.kind,
            label="Demo store — team-authored data" if st.kind == "demo" else "Owner-uploaded data"))
    return {"store_id": sid}


@app.get("/api/uploads/template.csv")
def upload_template():
    return Response(content=uploads.template_csv(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="store_upload_template.csv"'})


@app.post("/api/stores/{store_id}/uploads", dependencies=WRITE)
async def upload_sheet(store_id: str, file: UploadFile = File(...)):
    """Owner sheet → validate → first-party observations → real outcomes attached → the serving
    model is graded on the uploaded days it never saw → tomorrow's plan from real stock."""
    _require_uploadable(store_id)
    raw = await file.read(settings.max_upload_bytes + 1)
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(413, f"file larger than {settings.max_upload_bytes // (1024 * 1024)} MB")
    try:
        result = uploads.ingest(engine, store_id, raw, file.filename or "upload.csv")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result["accepted"]:
        result["evaluation"] = store_learning.evaluate_uploaded_days(engine, store_id, result["upload_id"])
        plan = store_learning.forecast_next_day(engine, store_id)
        result["next_forecast_date"] = plan["forecast_date"].iloc[0] if len(plan) else None
    _LEDGER_CACHE.clear()
    _BRIEFING_CACHE.pop(store_id, None)
    result["learning"] = store_learning.status(engine, store_id)
    return result


@app.get("/api/uploads/batch_template.csv")
def batch_template():
    return Response(content=uploads.batch_template_csv(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="store_batch_template.csv"'})


@app.post("/api/stores/{store_id}/batches", dependencies=WRITE)
async def upload_batches(store_id: str, file: UploadFile = File(...)):
    """Optional batch sheet: one row per delivery with its expiry date. Gives real expiry-aware
    stock for markdown planning, learned shelf lives, and exact sell-through per delivery."""
    _require_uploadable(store_id)
    raw = await file.read(settings.max_upload_bytes + 1)
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(413, f"file larger than {settings.max_upload_bytes // (1024 * 1024)} MB")
    try:
        result = uploads.ingest_batches(engine, store_id, raw, file.filename or "batches.csv")
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result["accepted"] and (settings.artifacts_dir / "store_recs" / f"{store_id}.parquet").exists():
        store_learning.forecast_next_day(engine, store_id)  # re-plan with the real expiry dates
    return result


@app.get("/api/stores/{store_id}/uploads")
def upload_history(store_id: str):
    with engine.connect() as conn:
        rows = conn.execute(select(S.uploads).where(S.uploads.c.store_id == store_id)
                            .order_by(S.uploads.c.created_at.desc())).all()
    return [dict(r._mapping) for r in rows]


@app.get("/api/stores/{store_id}/uploads/{upload_id}/quarantine.csv")
def upload_quarantine_csv(store_id: str, upload_id: str):
    with engine.connect() as conn:
        rows = conn.execute(select(S.quarantine).where(S.quarantine.c.table_name == f"upload:{upload_id}")).all()
    df = pd.DataFrame([{**(r.payload or {}), "reason": r.reason} for r in rows])
    return _csv(df, f"{store_id}_{upload_id}_rejected_rows.csv")


@app.get("/api/stores/{store_id}/learning")
def learning_status(store_id: str):
    try:
        st = store_learning.status(engine, store_id)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    st["sell_through"] = store_learning.sell_through(engine, store_id).replace({np.nan: None}).head(50).to_dict("records")
    st["adherence"] = store_learning.adherence(engine, store_id)
    lift = store_learning.store_lift(engine, store_id)
    st["measured_discount_response"] = {c: {f"{int(k * 100)}%": v for k, v in d.items()} for c, d in lift.items()}
    with engine.connect() as conn:
        st["promotion_experiments"] = conn.execute(select(func.count()).select_from(S.promotion_experiments)
                                                   .where(S.promotion_experiments.c.store_id == store_id)).scalar()
    st["eval_days"] = store_learning.EVAL_DAYS
    st["last_retrain"] = _RETRAIN_JOBS.get(store_id) or _last_retrain_from_db(store_id)
    return st


_RETRAIN_JOBS: dict[str, dict] = {}


def _last_retrain_from_db(store_id: str) -> dict | None:
    """Retrain outcomes survive restarts: read the latest one recorded for this store."""
    with engine.connect() as conn:
        r = conn.execute(select(S.retrain_runs).where(
            S.retrain_runs.c.context.in_([f"store:{store_id}", f"global_from:{store_id}"]))
            .order_by(S.retrain_runs.c.created_at.desc()).limit(1)).first()
    if not r:
        return None
    cmp = r.comparison or {}
    return {"state": "done", "decision": r.decision, "reason": r.reason, "candidate": r.candidate_version,
            "champion": r.champion_version,
            "champion_wape": ((cmp.get("champion") or {}).get("global") or {}).get("wape"),
            "challenger_wape": ((cmp.get("challenger") or {}).get("global") or {}).get("wape"),
            "finished_at": str(r.created_at)}


@app.post("/api/stores/{store_id}/retrain", dependencies=WRITE)
def retrain(store_id: str, background: BackgroundTasks):
    """Train a store challenger in the background; poll GET /api/stores/{id}/learning."""
    job = _RETRAIN_JOBS.get(store_id)
    if job and job.get("state") == "running":
        return job
    _RETRAIN_JOBS[store_id] = {"state": "running", "started_at": datetime.now(timezone.utc).isoformat()}

    def run():
        try:
            out = store_learning.retrain_store(engine, store_id)  # re-plans every store if promoted
            _LEDGER_CACHE.clear()
            _BRIEFING_CACHE.clear()
            _RETRAIN_JOBS[store_id] = {"state": "done", **out}
        except Exception as e:  # noqa: BLE001 — surface the failure to the owner
            _RETRAIN_JOBS[store_id] = {"state": "failed", "error": f"{type(e).__name__}: {e}"}
    background.add_task(run)
    return _RETRAIN_JOBS[store_id]


def _csv(df: pd.DataFrame, filename: str) -> Response:
    return Response(content=df.to_csv(index=False), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@app.get("/api/stores/{store_id}/plan.csv")
def plan_csv(store_id: str, waste_cost_ratio: float = Query(default=None, ge=0.05, le=2.0)):
    rec = recommendations(store_id, waste_cost_ratio=waste_cost_ratio, category=None)
    cols = ["forecast_date", "store_id", "product_id", "name", "category", "p50", "p80", "on_hand",
            "expiring_tomorrow", "order_qty", "waste_risk", "surplus_units", "markdown", "schedule_text",
            "surplus_action", "donate_units", "donate_date", "lift_source", "model_version"]
    df = pd.DataFrame(rec["items"])
    df = df[[c for c in cols if c in df.columns]].rename(columns={"p50": "forecast_units", "p80": "forecast_p80"})
    return _csv(df, f"{store_id}_order_plan_{rec['forecast_date']}.csv")


@app.get("/api/ledger.csv")
def ledger_csv(context: str = "production", store: str | None = None):
    df = _ledger(context, store)
    df["forecast_date"] = df["forecast_date"].dt.strftime("%Y-%m-%d")
    df["error"] = df["predicted_units"] - df["actual_units_sold"]
    cols = ["prediction_id", "context", "store_id", "product_id", "category", "forecast_date", "prediction_created_at",
            "model_version", "predicted_units", "p80_units", "actual_units_sold", "error", "waste_units", "observed_at"]
    return _csv(df[[c for c in cols if c in df.columns]], f"ledger_{context}{'_' + store if store else ''}.csv")


@app.get("/api/stores/{store_id}/sell-through.csv")
def sell_through_csv(store_id: str):
    return _csv(store_learning.sell_through(engine, store_id), f"{store_id}_sell_through.csv")


@app.get("/api/impact")
def impact_summary():
    """Social-good impact of the model's ordering vs. naive ordering over the production holdout
    (simulated waste), converted with cited factors and labeled assumptions."""
    rep = _read_json("production_report.json")
    by_cat = {r["category"]: r for r in rep["simulation"]["by_category"]}
    avoided = {c: max(r["baseline_waste"] - r["model_waste"], 0.0) for c, r in by_cat.items()}
    days = (pd.Timestamp(rep["holdout"][1]) - pd.Timestamp(rep["holdout"][0])).days + 1
    stores = len(STORE_LOCATIONS)
    out = impact.units_to_impact(avoided)
    rec_path = settings.artifacts_dir / "recommendations.parquet"
    products_per_store = round(len(pd.read_parquet(rec_path, columns=["store_id"])) / stores) if rec_path.exists() else None
    return {"holdout": rep["holdout"], "days": days, "stores": stores, "products_per_store": products_per_store,
            "scope": f"Covers the ~{products_per_store} sampled fresh products per store, not the whole assortment.",
            "waste_avoided": out,
            "per_store_per_year": {k: (v / days * 365 / stores if isinstance(v, (int, float)) else v) for k, v in out.items()},
            "lost_sales": {"model": rep["simulation"]["totals"]["model_lost"], "naive": rep["simulation"]["totals"]["baseline_lost"]},
            "sources": impact.sources(), "label": rep["simulation"]["label"]}


@app.get("/api/replay/summary")
def replay_summary():
    return _read_json("replay_summary.json")


@app.get("/api/production/report")
def production_report():
    return _read_json("production_report.json")


class TrafficRow(BaseModel):
    store_id: str
    timestamp: datetime
    customers_entered: float | None = None
    transactions: float | None = None
    units_sold: float | None = None
    revenue: float | None = None


def _ingest_traffic(df: pd.DataFrame, source: str) -> dict:
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce", utc=True)
    clean, bad = validate_traffic(df)
    now = datetime.now(timezone.utc)
    if len(clean):  # a (store, timestamp) already stored is a duplicate, not an update
        with engine.connect() as conn:
            seen = {(r.store_id, pd.Timestamp(r.timestamp).tz_localize(None) if pd.Timestamp(r.timestamp).tzinfo is None
                     else pd.Timestamp(r.timestamp).tz_convert(None))
                    for r in conn.execute(select(S.traffic_observations.c.store_id, S.traffic_observations.c.timestamp)
                                          .where(S.traffic_observations.c.store_id.in_(clean["store_id"].unique().tolist())))}
        key = [(s_, t.tz_convert(None)) in seen for s_, t in zip(clean["store_id"], clean["timestamp"])]
        dup = pd.Series(key, index=clean.index)
        if dup.any():
            bad = pd.concat([bad, clean[dup].assign(reason="duplicate_record")])
            clean = clean[~dup]
    with engine.begin() as conn:
        if len(clean):
            recs = clean.assign(source=source, ingested_at=now).replace({np.nan: None}).to_dict("records")
            conn.execute(insert(S.traffic_observations), recs)
            conn.execute(update(S.stores).where(S.stores.c.store_id.in_(clean["store_id"].unique().tolist()))
                         .values(traffic_connected=True))
        if len(bad):
            conn.execute(insert(S.quarantine), [
                {"table_name": "traffic_observations", "payload": json.loads(r.drop(labels=["reason"]).to_json(date_format="iso")),
                 "reason": r["reason"], "ingested_at": now} for _, r in bad.iterrows()])
    return {"accepted": int(len(clean)), "quarantined": int(len(bad)),
            "reasons": bad["reason"].value_counts().to_dict() if len(bad) else {}}


@app.post("/api/traffic", dependencies=WRITE)
def post_traffic(rows: list[TrafficRow]):
    return _ingest_traffic(pd.DataFrame([r.model_dump() for r in rows]), source="api")


@app.post("/api/traffic/csv", dependencies=WRITE)
async def post_traffic_csv(file: UploadFile = File(...)):
    raw = await file.read(settings.max_upload_bytes + 1)
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(413, f"CSV larger than {settings.max_upload_bytes // (1024 * 1024)} MB")
    df = pd.read_csv(io.BytesIO(raw))
    missing = {"store_id", "timestamp"} - set(df.columns)
    if missing:
        raise HTTPException(400, f"missing columns: {sorted(missing)}")
    return _ingest_traffic(df, source="csv")


class PromotionStart(BaseModel):
    store_id: str
    product_id: str
    discount: float
    start_time: datetime
    end_time: datetime
    inventory_before: float
    forecast_without_promotion: float


class PromotionOutcome(BaseModel):
    actual_sales: float
    inventory_after: float
    waste_after: float
    revenue: float


@app.post("/api/promotions", dependencies=WRITE)
def start_promotion(p: PromotionStart):
    """A manager accepted a markdown. Record it as an experiment so the store's real response to
    THIS discount on THIS category at THIS time can later replace the heuristic."""
    if not 0 < p.discount < 1:
        raise HTTPException(400, "discount must be a fraction in (0, 1); markdowns only lower prices")
    exp_id = uuid.uuid4().hex[:12]
    with engine.begin() as conn:
        conn.execute(insert(S.promotion_experiments).values(experiment_id=exp_id, **p.model_dump()))
    return {"experiment_id": exp_id}


@app.post("/api/promotions/{experiment_id}/outcome", dependencies=WRITE)
def close_promotion(experiment_id: str, o: PromotionOutcome):
    with engine.begin() as conn:
        row = conn.execute(select(S.promotion_experiments).where(S.promotion_experiments.c.experiment_id == experiment_id)).first()
        if not row:
            raise HTTPException(404, "unknown experiment")
        if row.actual_sales is not None:
            raise HTTPException(409, "outcome already recorded")
        conn.execute(update(S.promotion_experiments).where(S.promotion_experiments.c.experiment_id == experiment_id)
                     .values(**o.model_dump()))
    lift = (o.actual_sales / row.forecast_without_promotion - 1) if row.forecast_without_promotion else None
    return {"experiment_id": experiment_id, "observed_lift": lift}


@app.get("/api/promotions")
def list_promotions(store: str | None = None):
    q = select(S.promotion_experiments)
    if store:
        q = q.where(S.promotion_experiments.c.store_id == store)
    with engine.connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(q)]
    for r in rows:
        f, a = r.get("forecast_without_promotion"), r.get("actual_sales")
        r["observed_lift"] = (a / f - 1) if (a is not None and f) else None
    return rows


@app.get("/api/anomaly-days")
def anomaly_days(store: str | None = None):
    q = select(S.anomaly_days).order_by(S.anomaly_days.c.date)
    if store:
        q = q.where(S.anomaly_days.c.store_id == store)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(q)]


class OutcomeRow(BaseModel):
    prediction_id: str
    actual_units_sold: float | None = None
    actual_customer_count: float | None = None
    actual_inventory_remaining: float | None = None
    waste_units: float | None = None


@app.post("/api/outcomes", dependencies=WRITE)
def post_outcomes(rows: list[OutcomeRow]):
    """Attach observed outcomes to existing predictions. Predictions themselves are never edited."""
    now = datetime.now(timezone.utc)
    accepted, rejected = 0, []
    with engine.begin() as conn:
        for r in rows:
            exists = conn.execute(select(S.predictions.c.prediction_id).where(S.predictions.c.prediction_id == r.prediction_id)).first()
            dup = conn.execute(select(S.outcomes.c.prediction_id).where(S.outcomes.c.prediction_id == r.prediction_id)).first()
            bad = [k for k in ("actual_units_sold", "actual_customer_count", "waste_units")
                   if getattr(r, k) is not None and getattr(r, k) < 0]
            if not exists or dup or bad:
                reason = "unknown_prediction" if not exists else "duplicate_outcome" if dup else f"negative:{','.join(bad)}"
                conn.execute(insert(S.quarantine).values(table_name="outcomes", payload=r.model_dump(), reason=reason, ingested_at=now))
                rejected.append({"prediction_id": r.prediction_id, "reason": reason})
                continue
            conn.execute(insert(S.outcomes).values(**r.model_dump(), observed_at=now))
            accepted += 1
    return {"accepted": accepted, "rejected": rejected}


# Serve the built dashboard from the same origin in deployment (one container, one URL).
_DIST = settings.artifacts_dir.parent / "frontend" / "dist"
if _DIST.exists():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "unknown API route")
        f = _DIST / path
        return FileResponse(f if path and f.is_file() else _DIST / "index.html")
