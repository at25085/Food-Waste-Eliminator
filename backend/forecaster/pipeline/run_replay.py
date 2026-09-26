"""CLI: prepare data → feature table → historical replay → ledger/registry in the database.

    python -m forecaster.pipeline.run_replay [--per-store 120] [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import pandas as pd

from forecaster.data.prepare import prepare, processed_dir
from forecaster.features.build import FEATURE_SCHEMA_VERSION
from forecaster.db.schema import create_all, get_engine
from forecaster.pipeline import replay
from forecaster.pipeline.dataset import feature_table
from forecaster.seed import seed_anomaly_days, seed_stores


def load_features(per_store: int, force: bool) -> pd.DataFrame:
    path = processed_dir() / "features_h1.parquet"
    if path.exists() and not force and features_schema() == FEATURE_SCHEMA_VERSION:
        return pd.read_parquet(path)
    frames = prepare(per_store=per_store, force=force)
    feat, s1 = feature_table(frames)
    feat.to_parquet(path, index=False)
    (processed_dir() / "features_h1.meta.json").write_text(json.dumps({"feature_schema_version": FEATURE_SCHEMA_VERSION}))
    s1.to_parquet(processed_dir() / "stage1_h1.parquet", index=False)
    return feat


def features_schema() -> str | None:
    meta = processed_dir() / "features_h1.meta.json"
    return json.loads(meta.read_text())["feature_schema_version"] if meta.exists() else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-store", type=int, default=120)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    t0 = time.time()
    feat = load_features(args.per_store, args.force)
    for c in ("store_id", "category", "category_l2", "product_id"):
        feat[c] = feat[c].astype("category")
    engine = get_engine()
    create_all(engine)
    seed_stores(engine)
    seed_anomaly_days(engine)
    summary = replay.run(feat, engine, log=lambda m: print(f"[{time.time() - t0:6.0f}s] {m}", flush=True))
    promoted = sum(d["decision"] == "promoted" for d in summary["decisions"])
    print(f"done: {len(summary['decisions'])} challengers, {promoted} promoted, "
          f"final champion {summary['final_champion']} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
