"""Immutable model registry on disk. A version is written once and never modified; the only
mutable state is which version is champion (with full history for rollback)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import xgboost as xgb

from forecaster.config import settings


def models_dir() -> Path:
    d = settings.artifacts_dir / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def next_version(prefix: str = "demand") -> str:
    existing = [p.name for p in models_dir().glob(f"{prefix}_v*")]
    nums = [int(n.rsplit("_v", 1)[1]) for n in existing if n.rsplit("_v", 1)[1].isdigit()]
    return f"{prefix}_v{max(nums, default=0) + 1}"


def save(version: str, booster: xgb.Booster, meta: dict, extra_models: dict[str, xgb.Booster] | None = None) -> Path:
    path = models_dir() / version
    if path.exists():
        raise FileExistsError(f"model version {version} already exists; versions are immutable")
    path.mkdir(parents=True)
    booster.save_model(path / "model.ubj")
    for name, m in (extra_models or {}).items():
        m.save_model(path / f"{name}.ubj")
    meta = {**meta, "version": version, "created_at": datetime.now(timezone.utc).isoformat()}
    (path / "meta.json").write_text(json.dumps(meta, indent=2, default=str))
    return path


def load(version: str) -> tuple[xgb.Booster, dict, dict[str, xgb.Booster]]:
    path = models_dir() / version
    meta = json.loads((path / "meta.json").read_text())
    booster = xgb.Booster()
    booster.load_model(path / "model.ubj")
    extras = {}
    for p in path.glob("*.ubj"):
        if p.stem != "model":
            m = xgb.Booster()
            m.load_model(p)
            extras[p.stem] = m
    return booster, meta, extras


def purge_prefix(prefix: str) -> None:
    """Remove every version under a namespace. Only used to regenerate a backtest; production
    versions (a different prefix) are never touched."""
    import shutil
    for p in models_dir().glob(f"{prefix}_v*"):
        shutil.rmtree(p)


def list_versions() -> list[dict]:
    out = []
    for p in sorted(models_dir().glob("*_v*"), key=lambda p: int(p.name.rsplit("_v", 1)[1])):
        out.append(json.loads((p / "meta.json").read_text()))
    return out
