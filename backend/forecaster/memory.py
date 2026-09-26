"""Manager memory: what the store manager knows that the data doesn't ("street festival
Saturday", "we always overstock bread for the Friday church order").

Notes are stored append-only in our database (they double as candidate local-event records for
future retraining) and mirrored to Backboard, which gives each store's assistant persistent,
searchable long-term memory. Notes explain and flag; they never change forecast numbers — the
model has no evidence yet that a given kind of note predicts demand.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

import httpx
from sqlalchemy import insert, select, update

from forecaster.config import settings
from forecaster.db import schema as S

_STATE = settings.artifacts_dir / "backboard_assistants.json"


def _headers() -> dict:
    return {"X-API-Key": settings.backboard_api_key or "", "Content-Type": "application/json"}


def _assistant_id(store_id: str) -> str:
    """One Backboard assistant per store, created on first use and remembered locally."""
    state = json.loads(_STATE.read_text()) if _STATE.exists() else {}
    if store_id in state:
        return state[store_id]
    r = httpx.post(f"{settings.backboard_base_url}/assistants", headers=_headers(), timeout=30, json={
        "name": f"{settings.app_display_name} manager memory — {store_id}",
        "system_prompt": "You remember what a grocery store's fresh-department manager has told you "
                         "about their store: local events, standing orders, preferences and overrides.",
    })
    r.raise_for_status()
    state[store_id] = r.json()["assistant_id"]
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    _STATE.write_text(json.dumps(state, indent=2))
    return state[store_id]


def enabled() -> bool:
    return bool(settings.backboard_api_key)


def add_note(engine, store_id: str, content: str, kind: str, applies_on: date | None) -> dict:
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        note_id = conn.execute(insert(S.manager_notes).values(
            store_id=store_id, created_at=now, kind=kind, applies_on=applies_on, content=content)).inserted_primary_key[0]
    mirrored, error = False, None
    if enabled():
        try:
            meta = {"store_id": store_id, "kind": kind, "applies_on": str(applies_on) if applies_on else None,
                    "note_id": note_id}
            text = content if not applies_on else f"[{applies_on}] {content}"
            r = httpx.post(f"{settings.backboard_base_url}/assistants/{_assistant_id(store_id)}/memories",
                           headers=_headers(), json={"content": text, "metadata": meta}, timeout=30)
            r.raise_for_status()
            body = r.json() if r.content else {}
            mem_id = body.get("memory_id") or body.get("id")
            with engine.begin() as conn:
                conn.execute(update(S.manager_notes).where(S.manager_notes.c.id == note_id)
                             .values(backboard_memory_id=str(mem_id) if mem_id else "stored"))
            mirrored = True
        except Exception as e:  # noqa: BLE001 — the local record is the source of truth
            error = f"{type(e).__name__}: {e}"
    return {"id": note_id, "mirrored_to_backboard": mirrored, "backboard_error": error}


def list_notes(engine, store_id: str) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(select(S.manager_notes).where(S.manager_notes.c.store_id == store_id)
                            .order_by(S.manager_notes.c.created_at.desc())).all()
    return [dict(r._mapping) for r in rows]


def relevant_notes(engine, store_id: str, forecast_date: str, query: str, limit: int = 5) -> tuple[list[str], str]:
    """Notes relevant to tomorrow. Backboard semantic search when configured; otherwise local notes
    that are undated or dated within ±3 days of the forecast date."""
    if enabled():
        try:
            r = httpx.post(f"{settings.backboard_base_url}/assistants/{_assistant_id(store_id)}/memories/search",
                           headers=_headers(), json={"query": f"{forecast_date}: {query}", "limit": limit}, timeout=30)
            r.raise_for_status()
            body = r.json()
            items = body.get("memories", body.get("results", body)) if isinstance(body, dict) else body
            texts = [m.get("content") or m.get("memory") or "" for m in items if isinstance(m, dict)]
            return [t for t in texts if t][:limit], "backboard"
        except Exception:  # noqa: BLE001 — fall back to local notes
            pass
    fd = date.fromisoformat(forecast_date)
    out = []
    for n in list_notes(engine, store_id):
        if n["applies_on"] is None or abs((n["applies_on"] - fd).days) <= 3:
            out.append(n["content"] if n["applies_on"] is None else f"[{n['applies_on']}] {n['content']}")
    return out[:limit], "local"
