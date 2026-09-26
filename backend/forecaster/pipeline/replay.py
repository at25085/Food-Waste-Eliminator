"""Historical replay ("time machine"): operate the platform day by day over 2024-01-20 → end of
data using the weather forecasts actually issued the day before (Open-Meteo Previous Runs).

Everything written here is labeled context='replay' — it is a BACKTEST on historical data, never
presented as live customers. Each simulated day the champion writes predictions to the ledger,
the next day's actuals are attached as outcomes, and weekly the retraining policy decides whether
a challenger is trained and whether it is promoted or rejected.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import delete, insert, select

from forecaster.config import settings
from forecaster.db import schema as S
from forecaster.features.build import FEATURE_SCHEMA_VERSION
from forecaster.models import demand, registry
from forecaster.models.metrics import summarize
from forecaster.pipeline import lifecycle
from forecaster.seed import excluded_days

TRAIN_START = pd.Timestamp("2022-01-01")
REPLAY_START = pd.Timestamp("2024-01-20")  # first day with Previous Runs D+1 forecasts
EVAL_DAYS = 14
VERSION_PREFIX = "replay"  # backtest models are namespaced apart from production demand_v*
CHECK_EVERY_DAYS = 7
WEATHER_SNAPSHOT_COLS = ["weather_temperature_max", "weather_temperature_min",
                         "weather_precipitation_sum", "weather_code", "weather_wind_speed_max",
                         "weather_source"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _register(conn, version: str, cand: lifecycle.Candidate, status: str, reason: str,
              metrics: dict, context: str) -> None:
    conn.execute(insert(S.model_versions).values(
        version=version, stage="demand", training_start=cand.training_start.date(),
        training_end=cand.training_end.date(), feature_schema_version=FEATURE_SCHEMA_VERSION,
        params=cand.meta["params"], metrics=metrics,
        artifact_uri=str(registry.models_dir() / version), created_at=_now(),
        promotion_status=status, decision_reason=reason, context=context))


def run(feat: pd.DataFrame, engine, context: str = "replay", log=print) -> dict:
    rows = demand.training_rows(feat, excluded_days(engine))
    end = rows["date"].max()
    with engine.begin() as conn:  # idempotent re-run of the backtest
        ids = select(S.predictions.c.prediction_id).where(S.predictions.c.context == context)
        conn.execute(delete(S.outcomes).where(S.outcomes.c.prediction_id.in_(ids)))
        conn.execute(delete(S.predictions).where(S.predictions.c.context == context))
        conn.execute(delete(S.retrain_runs).where(S.retrain_runs.c.context == context))
        conn.execute(delete(S.champion_history).where(S.champion_history.c.context == context))
        conn.execute(delete(S.model_versions).where(S.model_versions.c.context == context))
    registry.purge_prefix(VERSION_PREFIX)  # a backtest re-run regenerates its own versions

    # Bootstrap champion: trained on everything before the replay starts.
    boot_cut = REPLAY_START - pd.Timedelta(days=1)
    champ = lifecycle.train_candidate(rows, boot_cut, TRAIN_START)
    version = registry.next_version(VERSION_PREFIX)
    test = rows[rows["date"] >= REPLAY_START]
    test_metrics = summarize(champ.predict(test), test["sales"])
    registry.save(version, champ.booster, {**champ.meta, "context": context, "train_metrics": champ.train_metrics,
                                           "test_metrics_full_replay_window": test_metrics},
                  lifecycle.extra_models(champ))
    with engine.begin() as conn:
        _register(conn, version, champ, "champion", "bootstrap model (Rohlik history + Open-Meteo archived forecasts)",
                  {"train": champ.train_metrics, "test": test_metrics}, context)
        conn.execute(insert(S.champion_history).values(context=context, version=version, effective_at=_now(),
                                                       as_of_date=boot_cut.date(), reason="bootstrap"))
    log(f"bootstrap {version}: train WAPE {champ.train_metrics['wape']:.3f}, replay-window WAPE {test_metrics['wape']:.3f}")

    champion = {"version": version, "cand": champ}
    timeline, decisions = [], []
    last_check = REPLAY_START
    for day in pd.date_range(REPLAY_START, end, freq="D"):
        day_rows = rows[rows["date"] == day]
        if not day_rows.empty:
            c = champion["cand"]
            p50 = c.predict(day_rows)
            p80 = c.predict_p80(day_rows, p50)
            _write_ledger(engine, context, day_rows, p50, p80, champion["version"], day)
            m = summarize(p50, day_rows["sales"])
            timeline.append({"date": str(day.date()), "version": champion["version"], **m})

        if (day - last_check).days >= CHECK_EVERY_DAYS:
            last_check = day
            d = _maybe_retrain(engine, context, rows, day, champion, log)
            if d:
                decisions.append(d)

    summary = {"context": context, "replay_start": str(REPLAY_START.date()), "replay_end": str(end.date()),
               "timeline": timeline, "decisions": decisions, "final_champion": champion["version"]}
    out = settings.artifacts_dir / f"{context}_summary.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    return summary


def _write_ledger(engine, context, day_rows, p50, p80, version, day) -> None:
    created = datetime.combine((day - pd.Timedelta(days=1)).date(), datetime.max.time()).replace(tzinfo=timezone.utc)
    preds, outs = [], []
    for (_, r), a, b in zip(day_rows.iterrows(), p50, p80):
        pid = uuid.uuid4().hex
        weather = {k: (None if pd.isna(r.get(k)) else (r.get(k) if isinstance(r.get(k), str) else float(r.get(k))))
                   for k in WEATHER_SNAPSHOT_COLS}
        preds.append(dict(prediction_id=pid, context=context, store_id=str(r["store_id"]),
                          product_id=str(r["product_id"]), category=str(r["category"]),
                          prediction_created_at=created, forecast_date=day.date(), horizon=1,
                          predicted_units=float(a), p80_units=float(b), weather_forecast=weather,
                          expected_customers=None if pd.isna(r["expected_customer_count"]) else float(r["expected_customer_count"]),
                          model_version=version, feature_schema_version=FEATURE_SCHEMA_VERSION))
        outs.append(dict(prediction_id=pid, actual_units_sold=float(r["sales"]),
                         actual_customer_count=None if pd.isna(r.get("customer_count")) else float(r["customer_count"]),
                         actual_inventory_remaining=None, waste_units=None,
                         observed_at=created + pd.Timedelta(days=1)))
    with engine.begin() as conn:
        conn.execute(insert(S.predictions), preds)
        conn.execute(insert(S.outcomes), outs)


def _maybe_retrain(engine, context, rows, day, champion, log) -> dict | None:
    champ = champion["cand"]
    if champ.meta.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        raise RuntimeError(f"champion {champion['version']} uses feature schema "
                           f"{champ.meta.get('feature_schema_version')}, code is {FEATURE_SCHEMA_VERSION}")
    cutoff = day - pd.Timedelta(days=EVAL_DAYS)
    new_days = max((cutoff - champ.training_end).days, 0)
    run_id = uuid.uuid4().hex[:12]
    base = dict(run_id=run_id, context=context, as_of_date=day.date(), trigger="weekly_evaluation",
                champion_version=champion["version"], new_observation_days=int(new_days), created_at=_now())
    if new_days < settings.min_new_days_for_retrain:
        with engine.begin() as conn:
            conn.execute(insert(S.retrain_runs).values(**base, decision="skipped",
                                                       reason=f"{new_days} new days < threshold {settings.min_new_days_for_retrain}"))
        return None

    cand = lifecycle.train_candidate(rows, cutoff, TRAIN_START)
    window = rows[(rows["date"] > cutoff) & (rows["date"] <= day)]
    champ_pred = champ.predict(window)
    cand_pred = cand.predict(window)
    champ_eval = lifecycle.evaluate(champ_pred, window)
    cand_eval = lifecycle.evaluate(cand_pred, window)
    base_eval = {k: lifecycle.evaluate(v.to_numpy(), window) for k, v in demand.baseline_predictions(window).items()}
    mid = cutoff + pd.Timedelta(days=EVAL_DAYS // 2)
    halves = []
    for m in [(window["date"] <= mid).to_numpy(), (window["date"] > mid).to_numpy()]:
        halves.append((summarize(champ_pred[m], window["sales"][m])["wape"],
                       summarize(cand_pred[m], window["sales"][m])["wape"]))
    decision, reason, checks = lifecycle.decide(champ_eval, cand_eval, base_eval, cand.train_metrics, halves)

    version = registry.next_version(VERSION_PREFIX)
    registry.save(version, cand.booster, {**cand.meta, "context": context, "train_metrics": cand.train_metrics,
                                          "eval_window": [str((cutoff + pd.Timedelta(days=1)).date()), str(day.date())],
                                          "decision": decision, "reason": reason}, lifecycle.extra_models(cand))
    comparison = {"champion": champ_eval, "challenger": cand_eval, "baselines": base_eval, "checks": checks,
                  "eval_window": [str((cutoff + pd.Timedelta(days=1)).date()), str(day.date())]}
    with engine.begin() as conn:
        _register(conn, version, cand, "champion" if decision == "promoted" else "rejected", reason,
                  {"train": cand.train_metrics, "eval": cand_eval["global"]}, context)
        conn.execute(insert(S.retrain_runs).values(**base, candidate_version=version, comparison=comparison,
                                                   decision=decision, reason=reason))
        if decision == "promoted":
            conn.execute(S.model_versions.update().where(S.model_versions.c.version == champion["version"])
                         .values(promotion_status="retired"))
            conn.execute(insert(S.champion_history).values(context=context, version=version, effective_at=_now(),
                                                           as_of_date=day.date(), reason=reason))
    log(f"{day.date()} {version}: {decision} — {reason}")
    if decision == "promoted":
        champion.update(version=version, cand=cand)
    return {"date": str(day.date()), "candidate": version, "decision": decision, "reason": reason,
            "champion_wape": champ_eval["global"]["wape"], "challenger_wape": cand_eval["global"]["wape"],
            "checks": checks}
