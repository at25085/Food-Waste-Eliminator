"""Morning manager briefing: Gemini turns tomorrow's plan into a short briefing. The LLM only
rephrases numbers we computed; it is given the facts and
told not to add any. Without API keys, a deterministic template produces the same content.
"""
from __future__ import annotations

import json

import httpx

from forecaster.config import settings

SYSTEM = (
    "You write the morning briefing for a grocery store's fresh department manager. Using ONLY the facts "
    "in the JSON you are given, write exactly four bullet points, each on its own line starting with '- ', "
    "each at most 16 words: (1) tomorrow's expected demand; (2) the items most at risk of waste and what "
    "to do (discount or donate); (3) the biggest order; (4) how accurate the forecasts have been lately. "
    "If manager_notes bear on tomorrow, fold them into the relevant bullet without changing any number. "
    "Say accuracy in plain words as a percentage from recent_model_error (e.g. 'forecasts were off by about 14%'); "
    "never use the words WAPE, champion, P50 or model version. Write numbers with thousands separators (33,326). "
    "Never invent numbers, items, causes or weather. No headings, no bold, nothing before or after the bullets."
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
        "note": ("Stock comes from the store's own sheets; forecasts are model output." if rec.get("real_stock")
                 else "Inventory and waste are simulated; forecasts are real model output."),
    }


def template(f: dict) -> str:
    """Deterministic four-bullet briefing (used without a key or when the model is unavailable)."""
    lines = [f"- Expect about {f['total_forecast_units']:,.0f} units of fresh demand on {f['forecast_date']}."]
    risky = f.get("waste_risk_items") or []
    if risky:
        r = risky[0]
        act = f"{r['suggested_markdown_pct']}% off" if r.get("suggested_markdown_pct") else "donate the surplus"
        lines.append(f"- {f['items_at_high_waste_risk']} items at high waste risk; {r['name']} first: {act}.")
    else:
        lines.append("- No items at high waste risk tomorrow.")
    if f.get("largest_orders"):
        o = f["largest_orders"][0]
        lines.append(f"- Largest order: {o['name']}, {o['order_qty']:,.0f} units.")
    else:
        lines.append("- No orders needed tomorrow.")
    r = f.get("recent_model_error")
    if r and r.get("wape") is not None:
        lines.append(f"- Forecasts were off by {100 * r['wape']:.0f}% over the last {r['days']} days.")
    else:
        lines.append("- Forecast accuracy appears after the first graded day.")
    return "\n".join(lines)


def ask_gemini(system: str, contents: list[dict]) -> tuple[str, str]:
    """One Gemini call through the model chain (primary, then fallback). Returns (text, model);
    raises RuntimeError naming each model's failure if none answered."""
    errors = []
    for model in dict.fromkeys(m for m in (settings.gemini_model, settings.gemini_fallback_model) if m):
        # No reasoning needed to reword facts: Gemini 2.x takes a thinking budget, 3.x a thinking level.
        thinking = {"thinkingBudget": 0} if model.startswith("gemini-2") else {"thinkingLevel": "minimal"}
        try:
            r = httpx.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                           headers={"x-goog-api-key": settings.gemini_api_key},
                           json={"systemInstruction": {"parts": [{"text": system}]}, "contents": contents,
                                 "generationConfig": {"temperature": 0.2, "thinkingConfig": thinking}},
                           timeout=settings.gemini_timeout_s)
            r.raise_for_status()
            text = "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"]).strip()
            if text:
                return text, model
            errors.append(f"{model}: empty answer")
        except Exception as e:  # noqa: BLE001 — try the next model
            code = getattr(getattr(e, "response", None), "status_code", None)
            errors.append(f"{model}: {type(e).__name__}{f' {code}' if code else ''}")
    raise RuntimeError("; ".join(errors))


def active_provider() -> str | None:
    return "gemini" if settings.gemini_api_key else None


def generate(facts: dict) -> tuple[str, str]:
    """Returns (text, provider). Falls back to the template if no key or every model fails."""
    if active_provider() is None:
        return template(facts), "template"
    try:
        text, model = ask_gemini(SYSTEM, [{"role": "user", "parts": [{"text": json.dumps(facts)}]}])
        return text, f"gemini:{model}"
    except Exception as e:  # noqa: BLE001 — the briefing must never break the dashboard
        return template(facts), f"template (gemini unavailable: {e})"
