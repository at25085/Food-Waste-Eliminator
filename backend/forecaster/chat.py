"""Ask-your-store chat: the owner asks in plain words, Gemini answers from this store's own data.

Every question gets a fresh data pack (tomorrow's plan, sell-through from the owner's sheets, recent
model accuracy, learning status, the manager notes most relevant to the question). The model may only
use that pack: it rephrases and points, it never invents numbers or changes the plan.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict, deque

import pandas as pd

from forecaster import briefing
from forecaster.config import settings

SYSTEM = """You are the assistant for the fresh department of a grocery store. The owner asks questions
in plain words. Answer using ONLY the DATA provided with each question. It has:
- PLAN: tomorrow's forecast per product from the store's demand model. p50 = expected sales, order_qty =
  recommended order (already net of stock still sellable), on_hand = stock entering tomorrow,
  expiring_tomorrow = units that expire tomorrow, waste_risk high/watch/low, surplus_units = stock
  likely to go unsold, discount_plan = the discount schedule chosen because it keeps the most money
  (it may leave some units to donate), donate_units = what that plan won't clear.
  lowest_discount_that_sells_all = the smallest discount (levels tested: 20/30/35/45/50/60%), started on the
  plan date and kept until the stock expires, at which the model predicts every unit sells before
  spoiling; "none up to 60%" means even 60% off leaves units to spoil (unsold_at_60 of them).
  money_at_that_discount = money kept vs doing nothing at that discount (negative = loses more than letting
  it spoil). Compare it with money_kept_by_discount_plan when the owner asks about selling everything.
  Money is in the store's currency (no symbol).
- SELL_THROUGH: from the owner's own uploaded sheets, per product: received, sold, wasted, and the share
  sold before spoiling. Absent if the store hasn't uploaded sheets.
- ACCURACY, LEARNING, NOTES, STORE.
Rules: never invent numbers, products, dates, events or causes. If the DATA doesn't answer the question,
say so and say where it would come from (upload a daily sheet, the Today's plan screen, the Upload screen's learning status).
You cannot change orders, prices or discounts; tell the owner where to do it (Today's plan). Manager
notes are context only: never adjust a number because of them. If STORE says the data is simulated or a
backtest, mention it when it matters. Be brief: 1-5 sentences, or a short list with "- " when listing
products. Plain text, no markdown headings or bold. Round to whole units."""

MAX_MESSAGE = 500
MAX_TURNS = 8
RATE_LIMIT = (20, 600)  # at most 20 questions per 10 minutes per client (protects the Gemini quota)
_hits: dict[str, deque] = defaultdict(deque)


def allowed(client: str) -> bool:
    n, window = RATE_LIMIT
    q, now = _hits[client], time.time()
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= n:
        return False
    q.append(now)
    return True


def _weather_us(w: dict) -> str:
    """Temperatures in °F and rain in inches, as the dashboard shows them."""
    f = lambda c: None if c is None else round(c * 9 / 5 + 32)  # noqa: E731
    rain = w.get("weather_precipitation_sum")
    return (f"high {f(w.get('weather_temperature_max'))}°F, low {f(w.get('weather_temperature_min'))}°F, "
            f"rain {None if rain is None else round(rain / 25.4, 2)} in")


def data_pack(rec: dict, store: dict | None, learning: dict | None, sell: pd.DataFrame,
              accuracy: dict | None, notes: list[str]) -> str:
    """Everything the assistant may use, as compact text (CSV tables keep the token count low)."""
    items = pd.DataFrame(rec.get("items", []))
    store = store or {}
    parts = [f"STORE: {store.get('store_id')} ({store.get('city')}). Data label: {store.get('label')}."]
    if len(items):
        cols = {"name": "product", "category": "category", "p50": "p50", "order_qty": "order_qty",
                "on_hand": "on_hand", "expiring_tomorrow": "expiring_tomorrow", "waste_risk": "waste_risk",
                "surplus_units": "surplus_units", "schedule_text": "discount_plan", "donate_units": "donate_units",
                "sell_price_main": "price", "money_kept_vs_no_action": "money_kept_by_discount_plan"}
        plan = items[[c for c in cols if c in items.columns]].rename(columns=cols)
        if "clear_all_discount" in items:
            surplus = items["surplus_units"].fillna(0) > 0.5
            plan["lowest_discount_that_sells_all"] = [
                ("" if not s else f"{round(d * 100)}%" if d == d and d is not None else "none up to 60%")
                for s, d in zip(surplus, items["clear_all_discount"])]
            plan["money_at_that_discount"] = items["clear_all_money_vs_no_action"]
            plan["unsold_at_60"] = items["clear_all_unsold_at_deepest"]
        rank = plan["waste_risk"].map({"high": 0, "watch": 1, "low": 2})
        plan = plan.assign(_r=rank).sort_values(["_r", "p50"], ascending=[True, False]).drop(columns="_r")
        cust = rec.get("recent_customers_7d")
        parts += [
            f"PLAN for {rec.get('forecast_date')} (model {rec.get('model_version')}): {len(items)} products, "
            f"expected sales {items['p50'].sum():.0f} units, recommended order {items['order_qty'].sum():.0f} units, "
            f"stock on hand entering tomorrow {items['on_hand'].sum():.0f} units, of which expiring tomorrow "
            f"{items['expiring_tomorrow'].sum():.0f}; surplus likely unsold {items.get('surplus_units', pd.Series(dtype=float)).fillna(0).sum():.0f} units "
            f"across {int((items['waste_risk'] != 'low').sum())} products at high/watch waste risk; "
            f"customers/day over the last 7 days {round(cust) if cust else 'unknown'}. Stock basis: "
            f"{rec.get('inventory_basis')} Weather tomorrow: {_weather_us(rec.get('weather') or {})}. "
            f"Donations planned: {rec.get('donations')}.",
            plan.to_csv(index=False, float_format="%.0f"),
        ]
    else:
        parts.append("PLAN: none yet (no forecast for this store).")
    if len(sell):
        s = sell.sort_values("wasted", ascending=False).head(25)
        s = s[["product_name", "category", "received", "sold", "wasted", "sell_through_before_spoilage", "shelf_life_days"]].copy()
        for c in ("received", "sold", "wasted", "shelf_life_days"):
            s[c] = s[c].round().astype("Int64")
        s["sell_through_before_spoilage"] = (100 * s["sell_through_before_spoilage"]).round(1).astype(str) + "%"
        parts += ["SELL_THROUGH (owner's sheets, most wasted first; sell_through_before_spoilage = share sold before spoiling):",
                  s.to_csv(index=False)]
    else:
        parts.append("SELL_THROUGH: none (no uploaded sheets for this store).")
    if accuracy and accuracy.get("wape") is not None:
        parts.append(f"ACCURACY over the last {accuracy['days']} graded days ({accuracy['source']}): weighted error "
                     f"(WAPE) {accuracy['wape']:.1%}, lower is better; bias {accuracy['bias']:+.1%} "
                     "(negative = forecasts lower than actual sales).")
    if learning:
        parts.append("LEARNING: " + ", ".join(f"{k}={learning.get(k)}" for k in (
            "serving_version", "has_learned_from_this_store", "uploaded_days", "last_day", "graded_forecasts",
            "new_days_since_model_training", "retrain_eligible")))
    parts.append("NOTES (the manager's own, context only): " + ("; ".join(notes) if notes else "none"))
    return "\n".join(parts)


# Replies may write thousands as "1,423"; the data pack is CSV, where a comma always separates values.
_NUM_REPLY = re.compile(r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?")
_NUM_DATA = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?%?")


def _numbers(text: str, pattern: re.Pattern = _NUM_REPLY) -> list[tuple[str, float, bool]]:
    out = []
    for m in pattern.finditer(text):
        tok = m.group(0)
        try:
            out.append((tok, float(tok.rstrip("%").replace(",", "")), tok.endswith("%")))
        except ValueError:
            pass
    return out


def unverified_numbers(reply: str, pack: str, history_text: str = "") -> list[str]:
    """Numbers in the reply that appear nowhere in the data (allowing rounding and share↔percent).
    Small counts (≤ 12, e.g. "3 products") and anything the owner typed are allowed."""
    known = [v for _, v, _ in _numbers(pack, _NUM_DATA)] + [v for _, v, _ in _numbers(history_text)]
    bad = []
    for tok, v, is_pct in _numbers(reply):
        if abs(v) <= 12 and float(v).is_integer():
            continue
        cands = [v] + ([v / 100] if is_pct else [])
        if not any(abs(c - k) <= max(0.51, 0.006 * abs(k)) for c in cands for k in known):
            bad.append(tok)
    return bad


def answer(pack: str, history: list[dict], message: str) -> tuple[str, str]:
    """Returns (reply, provider)."""
    if not settings.gemini_api_key:
        return ("The chat needs a Gemini key (GEMINI_API_KEY in backend/.env). Tomorrow's plan is on the "
                "Dashboard and Today's plan screens."), "none"
    contents = [{"role": "user" if h.get("role") == "user" else "model",
                 "parts": [{"text": str(h.get("text", ""))[:2000]}]} for h in history[-MAX_TURNS:]]
    contents.append({"role": "user", "parts": [{"text": f"DATA:\n{pack}\n\nQUESTION: {message}"}]})
    try:
        text, model = briefing.ask_gemini(SYSTEM, contents)
        said = message + " " + " ".join(str(h.get("text", "")) for h in history if h.get("role") == "user")
        bad = unverified_numbers(text, pack, said)
        if bad:  # one correction round: the model must drop or fix figures that aren't in the data
            contents += [{"role": "model", "parts": [{"text": text}]},
                         {"role": "user", "parts": [{"text": f"These figures are not in the DATA: {', '.join(bad)}. "
                                                             "Rewrite your answer using only numbers that appear in the DATA."}]}]
            text, model = briefing.ask_gemini(SYSTEM, contents)
            bad = unverified_numbers(text, pack, said)
            if bad:
                text += f"\n\n(Check these figures on Today's plan: {', '.join(bad)} could not be matched to your data.)"
        return text, f"gemini:{model}"
    except Exception as e:  # noqa: BLE001 — never break the page
        return f"Sorry, the assistant is unavailable right now ({e}). The plan itself is on Today's plan.", "unavailable"
