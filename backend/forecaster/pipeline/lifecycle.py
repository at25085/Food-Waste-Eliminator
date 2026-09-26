"""Champion/challenger: train a candidate on a data snapshot, evaluate on unseen recent data,
and promote only if it is meaningfully better without unacceptable regressions."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from forecaster.config import settings
from forecaster.features.build import FEATURE_SCHEMA_VERSION
from forecaster.models import demand
from forecaster.models.metrics import summarize

MAJOR_CATEGORIES = ("Fruit and vegetable", "Bakery", "Meat and fish")
ES_VALID_DAYS = 49  # early-stopping window inside the candidate's own training range


PRODUCE = "Fruit and vegetable"
DEFAULT_CONFIG = {"censor_stockouts": False, "produce_specialist": False, "store_calibration": False,
                  "volatility_weight": False}


@dataclass
class Candidate:
    booster: object
    meta: dict
    p80_booster: object
    p80_meta: dict
    training_start: pd.Timestamp
    training_end: pd.Timestamp
    train_metrics: dict = field(default_factory=dict)
    specialist: object | None = None  # produce-only booster
    specialist_meta: dict | None = None
    calibration: dict = field(default_factory=dict)  # store_id -> multiplier (L3 calibration)

    def predict(self, df: pd.DataFrame, drop_traffic: bool = False) -> np.ndarray:
        pred = demand.predict(self.booster, self.meta, df, drop_traffic=drop_traffic)
        if self.specialist is not None:
            m = (df["category"].astype(str) == PRODUCE).to_numpy()
            if m.any():
                pred[m] = demand.predict(self.specialist, self.specialist_meta, df[m], drop_traffic=drop_traffic)
        return pred * self._multipliers(df)

    def predict_p80(self, df: pd.DataFrame, p50: np.ndarray | None = None) -> np.ndarray:
        p80 = demand.predict(self.p80_booster, self.p80_meta, df) * self._multipliers(df)
        return np.maximum(p80, p50 if p50 is not None else self.predict(df))

    def _multipliers(self, df: pd.DataFrame) -> np.ndarray:
        if not self.calibration:
            return np.ones(len(df))
        return df["store_id"].astype(str).map(self.calibration).fillna(1.0).to_numpy(dtype="float64")


def _fit_refit(tr, va, snap, exclude, weights_fn):
    """Early stopping on `va` picks the tree count; then refit on the whole snapshot."""
    booster, meta = demand.fit(tr, va, exclude=exclude, weights=weights_fn(tr) if weights_fn else None)
    es_booster, es_meta = booster, dict(meta)
    es_fraction = len(va) / max(len(tr), 1)
    booster, meta = demand.refit(snap, meta, es_fraction, weights=weights_fn(snap) if weights_fn else None)
    return booster, meta, es_booster, es_meta, es_fraction


def _produce(d: pd.DataFrame) -> pd.DataFrame:
    return d[d["category"].astype(str) == PRODUCE]


def train_candidate(rows: pd.DataFrame, cutoff: pd.Timestamp, start: pd.Timestamp,
                    exclude: tuple[str, ...] = (), config: dict | None = None) -> Candidate:
    """rows: training_rows(feature table). Uses only data in [start, cutoff].

    config switches (each measured before being turned on by default):
      censor_stockouts   drop availability < 0.9 days as training targets
      produce_specialist separate Tweedie model for Fruit and vegetable
      store_calibration  per-store multiplier fitted out-of-sample on the last 28 days, clipped [0.9, 1.1]
      volatility_weight  weight 2 for spike-prone rows (top quartile of trailing 28-day std)
    """
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    snap = rows[(rows["date"] >= start) & (rows["date"] <= cutoff)]
    if cfg["censor_stockouts"]:
        snap = snap[~(snap["availability"] < 0.9)]
    es_start = cutoff - pd.Timedelta(days=ES_VALID_DAYS)
    tr, va = snap[snap["date"] <= es_start], snap[snap["date"] > es_start]
    wfn = demand.volatility_weights if cfg["volatility_weight"] else None
    booster, meta, es_booster, es_meta, es_fraction = _fit_refit(tr, va, snap, exclude, wfn)
    p80, p80_meta = demand.fit_quantile(tr, va, es_meta)
    p80, p80_meta = demand.refit(snap, p80_meta, es_fraction)

    spec = spec_meta = es_spec = es_spec_meta = None
    if cfg["produce_specialist"]:
        spec, spec_meta, es_spec, es_spec_meta, _ = _fit_refit(_produce(tr), _produce(va), _produce(snap), exclude, wfn)

    calibration = {}
    if cfg["store_calibration"]:
        # Out-of-sample: the early-stopping-stage models never trained on their validation window.
        tail = va[va["date"] > cutoff - pd.Timedelta(days=28)]
        es_cand = Candidate(es_booster, es_meta, p80, p80_meta, start, cutoff,
                            specialist=es_spec, specialist_meta=es_spec_meta)
        pred = es_cand.predict(tail)
        for store, g in tail.assign(_p=pred).groupby("store_id", observed=True):
            if g["_p"].sum() > 0:
                calibration[str(store)] = float(np.clip(g["sales"].sum() / g["_p"].sum(), 0.9, 1.1))

    cand = Candidate(booster, meta, p80, p80_meta, start, cutoff, specialist=spec, specialist_meta=spec_meta,
                     calibration=calibration)
    sample = snap.sample(min(len(snap), 150_000), random_state=1)
    cand.train_metrics = summarize(cand.predict(sample), sample["sales"])
    meta.update({"feature_schema_version": FEATURE_SCHEMA_VERSION, "config": cfg, "calibration": calibration,
                 "training_start": str(start.date()), "training_end": str(cutoff.date())})
    if spec_meta is not None:
        meta["specialist"] = {"best_iteration": spec_meta["best_iteration"]}
    return cand


def extra_models(c: Candidate) -> dict:
    out = {"p80": c.p80_booster}
    if c.specialist is not None:
        out["produce"] = c.specialist
    return out


def evaluate(pred: np.ndarray, window: pd.DataFrame) -> dict:
    out = {"global": summarize(pred, window["sales"]), "by_category": {}}
    for cat in MAJOR_CATEGORIES:
        m = window["category"].astype(str) == cat
        out["by_category"][cat] = summarize(pred[m.to_numpy()], window.loc[m, "sales"])
    return out


def decide(champion: dict, challenger: dict, baselines: dict[str, dict], train_metrics: dict,
           halves: list[tuple[float, float]] | None = None) -> tuple[str, str, dict]:
    """Return (decision, human-readable reason, checks). Newer does not mean better.

    halves: [(champion_wape, challenger_wape)] for each sub-window of the evaluation window; the
    challenger must not lose in any of them, so one lucky week cannot carry a promotion."""
    s = settings
    cw, nw = champion["global"]["wape"], challenger["global"]["wape"]
    improvement = (cw - nw) / cw
    checks = {
        "global_improvement": round(improvement, 4),
        "global_improvement_ok": improvement >= s.min_global_wape_improvement,
        "category_regressions": {},
        "beats_baselines": all(nw < b["global"]["wape"] for b in baselines.values()),
    }
    worst = 0.0
    for cat in MAJOR_CATEGORIES:
        c = champion["by_category"][cat]["wape"]
        n = challenger["by_category"][cat]["wape"]
        if c and n:
            deg = (n - c) / c
            checks["category_regressions"][cat] = round(deg, 4)
            worst = max(worst, deg)
    checks["category_ok"] = worst <= s.max_category_wape_degradation
    tw = train_metrics.get("wape")
    ratio = nw / tw if tw else None
    checks["overfit_ratio"] = round(ratio, 3) if ratio else None
    checks["overfit_ok"] = ratio is None or ratio <= s.overfit_ratio_warning
    checks["sub_windows"] = [{"champion": c, "challenger": n} for c, n in (halves or [])]
    checks["stable_ok"] = all(n <= c for c, n in (halves or []))

    if not checks["global_improvement_ok"]:
        return "rejected", (f"WAPE {nw:.3f} vs champion {cw:.3f} ({improvement:+.1%}); "
                            f"needs ≥{s.min_global_wape_improvement:.0%} improvement"), checks
    if not checks["category_ok"]:
        cat = max(checks["category_regressions"], key=checks["category_regressions"].get)
        return "rejected", f"{cat} degraded {checks['category_regressions'][cat]:+.1%} (limit {s.max_category_wape_degradation:.0%})", checks
    if not checks["beats_baselines"]:
        return "rejected", "does not beat both naive baselines", checks
    if not checks["stable_ok"]:
        return "rejected", "wins overall but loses in one of the two weeks of the evaluation window", checks
    if not checks["overfit_ok"]:
        return "rejected", f"overfit guard: eval/train WAPE ratio {ratio:.2f} > {s.overfit_ratio_warning}", checks
    return "promoted", f"WAPE {nw:.3f} vs champion {cw:.3f} ({improvement:+.1%}); no category regressed > {s.max_category_wape_degradation:.0%}", checks
