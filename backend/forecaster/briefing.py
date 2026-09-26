"""Morning manager briefing: Gemini turns tomorrow's plan into a short spoken-style briefing,
ElevenLabs reads it aloud. The LLM only rephrases numbers we computed; it is given the facts and
told not to add any. Without API keys, a deterministic template produces the same content.
"""
from __future__ import annotations

import json

import httpx

from forecaster.config import settings

SYSTEM = (
    "You are the morning briefing for a grocery store's fresh department manager. Using ONLY the "
    "facts in the JSON you are given, write a briefing of at most 120 words that a person can read "
    "aloud in under a minute: tomorrow's demand outlook, the 2–3 items most at risk of waste and the "
    "suggested markdowns, the biggest order changes, and one sentence on how reliable the model has "
    "been lately (its recent error and whether it has been over- or under-forecasting). If "
    "manager_notes are present, mention any that bear on tomorrow as things to keep in mind, but never "
    "change or adjust any number because of them. Never invent numbers, items, causes or weather that "
    "are not in the facts. Plain sentences, no lists, no markdown."
)


def build_facts(rec: dict, health: dict | None, recent: dict | None, notes: list[str] | None = None) -> dict:
    items = rec["items"]
    risky = [i for i in items if i["waste_risk"] == "high"][:3]
    top_orders = sorted(items, key=lambda i: -(i["order_qty"] or 0))[:3]
    return {
        "store": rec["store_id"], "forecast_date": rec["forecast_date"],
        "model_version": rec["model_version"],
        "customers_per_day_last_7_days": round(rec["recent_customers_7d"]) if rec.get("recent_customers_7d") else None,
        "weather": {k.replace("weather_", ""): v for k, v in rec["weather"].items() if v is not None},
        "total_forecast_units": round(sum(i["p50"] for i in items)),
        "items_at_high_waste_risk": len([i for i in items if i["waste_risk"] == "high"]),
        "waste_risk_items": [{"name": i["name"], "expiring_units": round(i["expiring_leftover"], 1),
                              "suggested_markdown_pct": round(100 * i["markdown"]),
                              "model_estimated_lift_pct": None if i["markdown_lift_estimate"] is None
                              else round(100 * i["markdown_lift_estimate"])} for i in risky],
        "largest_orders": [{"name": i["name"], "order_qty": round(i["order_qty"]), "forecast": round(i["p50"])}
                           for i in top_orders],
        "recent_model_error": recent,
        "champion_test_wape": (health or {}).get("metrics", {}).get("test", {}).get("wape"),
        "manager_notes": notes or [],
        "note": "Inventory and waste are simulated; forecasts are real model output.",
    }


def template(f: dict) -> str:
    parts = [f"Good morning. For {f['forecast_date']}, the model ({f['model_version']}) expects about "
             f"{f['total_forecast_units']} units of fresh demand"
             + (f", with about {f['customers_per_day_last_7_days']} customers a day lately."
                if f["customers_per_day_last_7_days"] else ".")]
    if f["waste_risk_items"]:
        names = ", ".join(f"{i['name']} ({i['suggested_markdown_pct']}% off)" for i in f["waste_risk_items"])
        parts.append(f"{f['items_at_high_waste_risk']} items are at high risk of waste; start with {names}.")
    else:
        parts.append("No items are at high risk of waste.")
    if f["largest_orders"]:
        o = f["largest_orders"][0]
        parts.append(f"Largest order: {o['name']}, {o['order_qty']} units against a forecast of {o['forecast']}.")
    r = f.get("recent_model_error")
    if r and r.get("wape") is not None:
        direction = "over-forecasting" if r["bias"] > 0 else "under-forecasting"
        parts.append(f"Over the last {r['days']} days the model's weighted error was {100 * r['wape']:.0f}%, "
                     f"slightly {direction} by {abs(100 * r['bias']):.0f}%.")
    if f.get("manager_notes"):
        parts.append("From your notes: " + "; ".join(f["manager_notes"][:2]) + ".")
    return " ".join(parts)


def _gemini(facts: dict) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.gemini_model}:generateContent"
    r = httpx.post(url, headers={"x-goog-api-key": settings.gemini_api_key},
                   json={"systemInstruction": {"parts": [{"text": SYSTEM}]},
                         "contents": [{"role": "user", "parts": [{"text": json.dumps(facts)}]}],
                         "generationConfig": {"temperature": 0.2}}, timeout=30)
    r.raise_for_status()
    return "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"]).strip()



def active_provider() -> str | None:
    return "gemini" if settings.gemini_api_key else None


def generate(facts: dict) -> tuple[str, str]:
    """Returns (text, provider). Falls back to the template if no key or the call fails."""
    provider = active_provider()
    if provider is None:
        return template(facts), "template"
    try:
        return _gemini(facts), f"gemini:{settings.gemini_model}"
    except Exception as e:  # noqa: BLE001 — the briefing must never break the dashboard
        return template(facts), f"template ({provider} unavailable: {type(e).__name__})"


def speak(text: str) -> bytes | None:
    if not settings.elevenlabs_api_key:
        return None
    r = httpx.post(f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}",
                   headers={"xi-api-key": settings.elevenlabs_api_key, "Accept": "audio/mpeg"},
                   json={"text": text, "model_id": settings.elevenlabs_model}, timeout=60)
    r.raise_for_status()
    return r.content
