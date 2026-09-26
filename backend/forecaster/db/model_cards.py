"""Model cards in MongoDB Atlas: one self-contained document per model version.

The time-series ledger (predictions, outcomes, rolling error) lives in PostgreSQL/TimescaleDB,
where joins and window aggregates belong. A model card is naturally a nested document — training
range, parameters, feature schema, feature importance, the champion-vs-challenger evaluation with
per-category checks, and the promotion decision — so it lives in a document store.

    python -m forecaster.db.model_cards      # (re)build cards for every version, upsert to Atlas
"""
from __future__ import annotations

import json
from datetime import date, datetime

import xgboost as xgb
from sqlalchemy import select

from forecaster.config import settings
from forecaster.db import schema as S
from forecaster.db.schema import get_engine
from forecaster.models import registry


def _jsonable(v):
    return json.loads(json.dumps(v, default=lambda o: o.isoformat() if isinstance(o, (date, datetime)) else str(o)))


def build_cards(engine, only: str | None = None) -> list[dict]:
    with engine.connect() as conn:
        q = select(S.model_versions)
        if only:
            q = q.where(S.model_versions.c.version == only)
        versions = [dict(r._mapping) for r in conn.execute(q)]
        runs = {r.candidate_version: dict(r._mapping) for r in conn.execute(select(S.retrain_runs))
                if r.candidate_version}
        history = [dict(r._mapping) for r in conn.execute(select(S.champion_history))]
    cards = []
    for v in versions:
        path = registry.models_dir() / v["version"]
        meta = json.loads((path / "meta.json").read_text()) if (path / "meta.json").exists() else {}
        importance = meta.get("feature_importance", [])
        if not importance and (path / "model.ubj").exists():
            b = xgb.Booster()
            b.load_model(path / "model.ubj")
            gain = b.get_score(importance_type="total_gain")
            total = sum(gain.values()) or 1.0
            importance = [{"feature": k, "share": g / total}
                          for k, g in sorted(gain.items(), key=lambda kv: -kv[1])[:20]]
        run = runs.get(v["version"])
        cards.append(_jsonable({
            "_id": v["version"],
            "context": v["context"],
            "status": v["promotion_status"],
            "decision_reason": v["decision_reason"],
            "training_range": [v["training_start"], v["training_end"]],
            "feature_schema_version": v["feature_schema_version"],
            "feature_names": meta.get("feature_names"),
            "params": v["params"],
            "trees": meta.get("refit_rounds", meta.get("best_iteration")),
            "metrics": v["metrics"],
            "feature_importance": importance,
            "evaluation": run["comparison"] if run else None,
            "evaluated_on": run["as_of_date"] if run else None,
            "champion_periods": [h for h in history if h["version"] == v["version"]],
            "created_at": v["created_at"],
        }))
    return cards


def collection():
    from pymongo import MongoClient
    if not settings.mongodb_uri:
        return None
    return MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)[settings.mongodb_db]["model_cards"]


def sync(engine=None) -> int:
    coll = collection()
    if coll is None:
        raise SystemExit("set MONGODB_URI in backend/.env")
    cards = build_cards(engine or get_engine())
    for c in cards:
        coll.replace_one({"_id": c["_id"]}, c, upsert=True)
    coll.create_index([("context", 1), ("status", 1)])
    return len(cards)


if __name__ == "__main__":
    print(f"upserted {sync()} model cards")
