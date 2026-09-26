"""Markdown schedule: the discount that loses the least money on stock that won't sell in time.

For each product, stock is a list of cohorts (units, sellable days left incl. tomorrow) — from the
owner's batch sheet (real expiry dates) or estimated first-in-first-out from the daily sheet.

Every option "d% off from day s until the stock expires" (plus "no discount") is simulated day by
day with the TRAINED demand model's prediction at that discount:
    money = Σ_days  price_t × units_sold_t           (a discount applies to every unit sold that day,
                                                      including ones that would have sold anyway)
          − cost × units bought to cover demand beyond current stock
          − disposal × units that still spoil
The option with the most money wins; what still spoils is routed to donation. Demand for days after
tomorrow is tomorrow's forecast carried forward — the model forecasts one day ahead, so this is an
approximation and is labeled as one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

LEVELS = (0.2, 0.3, 0.35, 0.45, 0.5, 0.6)
DEFAULT_LIFT = {0.2: 0.10, 0.3: 0.18, 0.35: 0.22, 0.45: 0.30, 0.5: 0.35, 0.6: 0.45}  # conservative prior
COST_RATIO = 0.7  # unit cost as a share of price (≈30% fresh gross margin)
DISPOSAL_RATIO = 0.02  # handling/disposal cost per spoiled unit, as a share of price


@dataclass
class Step:
    day: int  # 0 = tomorrow
    discount: float
    units: float  # surplus units this discount clears (vs. no discount)
    days: int = 1  # how many days the discount runs


@dataclass
class Plan:
    surplus_units: float
    steps: list[Step] = field(default_factory=list)
    donate_units: float = 0.0
    donate_day: int | None = None
    lift_source: str = "none"
    money_no_action: float = 0.0
    money_plan: float = 0.0
    revenue_given_up: float = 0.0  # discount on units that would have sold at full price anyway


def monotone(lift: dict[float, float]) -> dict[float, float]:
    """Deeper discounts never sell less: cumulative max over levels, floored at 0."""
    out, best = {}, 0.0
    for lv in LEVELS:
        best = max(best, lift.get(lv, 0.0) or 0.0)
        out[lv] = best
    return out


def project_unsold(cohorts: list[tuple[float, int]], daily_demand: float) -> list[tuple[float, int]]:
    """No discount, FIFO: oldest cohorts sell first. Returns [(units unsold at expiry, days_left)]."""
    out, used = [], 0.0
    for units, days_left in sorted(cohorts, key=lambda c: c[1]):
        sold = min(units, max(daily_demand * max(days_left, 0) - used, 0.0))
        used += sold
        if units - sold > 1e-9:
            out.append((units - sold, days_left))
    return out


def _simulate(cohorts, daily_demand, price, discount, start, horizon, lift, cost_ratio, disposal_ratio):
    stock = sorted([[u, d] for u, d in cohorts if u > 1e-9], key=lambda c: c[1])
    money, wasted, discounted_full_price_units = 0.0, 0.0, 0.0
    for t in range(horizon):
        on = discount > 0 and t >= start
        p = price * (1 - discount) if on else price
        demand = daily_demand * (1 + (lift.get(discount, 0.0) if on else 0.0))
        remaining = demand
        for c in stock:  # oldest first; cohort usable while t < days_left
            if c[1] > t and remaining > 0:
                take = min(c[0], remaining)
                c[0] -= take
                remaining -= take
        bought = remaining  # demand beyond current stock is covered by new deliveries
        money += p * demand - cost_ratio * price * bought
        if on:
            discounted_full_price_units += min(daily_demand, demand)
        for c in stock:  # cohorts whose last sellable day was t spoil tonight
            if c[1] == t + 1 and c[0] > 1e-9:
                wasted += c[0]
                c[0] = 0.0
    money -= disposal_ratio * price * wasted
    return money, wasted, discounted_full_price_units * price * discount


def plan(cohorts: list[tuple[float, int]], daily_demand: float, price: float = 1.0,
         lift_at: dict[float, float] | None = None, lift_source: str = "default",
         cost_ratio: float = COST_RATIO, disposal_ratio: float = DISPOSAL_RATIO) -> Plan:
    lift = monotone(lift_at or DEFAULT_LIFT)
    cohorts = [(u, d) for u, d in cohorts if u > 1e-9 and d >= 1]
    unsold = project_unsold(cohorts, daily_demand)
    surplus = sum(u for u, _ in unsold)
    if not cohorts:
        return Plan(surplus_units=0.0)
    horizon = max(d for _, d in cohorts)
    base_money, base_waste, _ = _simulate(cohorts, daily_demand, price, 0.0, 0, horizon, lift, cost_ratio, disposal_ratio)
    best = (base_money, 0.0, 0, base_waste, 0.0)
    if surplus > 1e-9 and daily_demand > 0:
        for d in LEVELS:
            if lift.get(d, 0.0) <= 0:
                continue
            for s in range(horizon):
                m, w, given_up = _simulate(cohorts, daily_demand, price, d, s, horizon, lift, cost_ratio, disposal_ratio)
                if m > best[0] + 1e-9:
                    best = (m, d, s, w, given_up)
    money, d, s, waste, given_up = best
    p = Plan(surplus_units=surplus, lift_source=lift_source if surplus > 0 else "none",
             money_no_action=base_money, money_plan=money, revenue_given_up=given_up)
    if d > 0:
        p.steps.append(Step(day=s, discount=d, units=max(surplus - waste, 0.0), days=horizon - s))
    if waste > 1e-9:
        p.donate_units = waste
        p.donate_day = min((dl for u, dl in unsold), default=1) - 1
    return p


@dataclass
class ClearAll:
    discount: float | None  # lowest level that sells every unit before expiry; None if even the deepest doesn't
    money_vs_no_action: float | None  # money at that level minus doing nothing (negative = worse than letting it spoil)
    waste_at_deepest: float  # units still spoiling at the deepest level (when discount is None)


def lowest_clearing_discount(cohorts: list[tuple[float, int]], daily_demand: float, price: float = 1.0,
                             lift_at: dict[float, float] | None = None, cost_ratio: float = COST_RATIO,
                             disposal_ratio: float = DISPOSAL_RATIO, tolerance: float = 0.5) -> ClearAll | None:
    """The owner's question "what's the smallest discount at which it all still sells?": each level is
    started on the first plan day (the most selling days, so the shallowest depth) and kept until the
    stock expires, using the model's predicted sales at that discount. None when nothing would spoil."""
    lift = monotone(lift_at or DEFAULT_LIFT)
    cohorts = [(u, d) for u, d in cohorts if u > 1e-9 and d >= 1]
    if not cohorts or sum(u for u, _ in project_unsold(cohorts, daily_demand)) <= tolerance:
        return None
    horizon = max(d for _, d in cohorts)
    base, _, _ = _simulate(cohorts, daily_demand, price, 0.0, 0, horizon, lift, cost_ratio, disposal_ratio)
    waste = 0.0
    for d in LEVELS:
        m, waste, _ = _simulate(cohorts, daily_demand, price, d, 0, horizon, lift, cost_ratio, disposal_ratio)
        if waste <= tolerance:
            return ClearAll(discount=d, money_vs_no_action=m - base, waste_at_deepest=0.0)
    return ClearAll(discount=None, money_vs_no_action=None, waste_at_deepest=waste)


def describe(p: Plan, name: str, dates: list[str], currency: str = "") -> str:
    """Human sentence for the plan, e.g. for the briefing and the CSV."""
    if p.surplus_units <= 0:
        return f"{name}: sells through before expiry."
    parts = [f"{name}: ~{p.surplus_units:.0f} units won't sell before expiry at full price"]
    for s in p.steps:
        parts.append(f"{int(s.discount * 100)}% off from {dates[min(s.day, len(dates) - 1)]} for {s.days} day(s) "
                     f"clears ~{s.units:.0f} (keeps {currency}{p.money_plan - p.money_no_action:,.0f} more than doing nothing)")
    if not p.steps:
        parts.append("no discount pays for itself")
    if p.donate_units > 0 and p.donate_day is not None:
        parts.append(f"donate ~{p.donate_units:.0f} on {dates[min(max(p.donate_day, 0), len(dates) - 1)]}")
    return "; ".join(parts) + "."
