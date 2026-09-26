"""Turn forecasts into actions: order quantity, waste risk, markdown suggestion, and a
transparent inventory simulator (waste is SIMULATED — no public dataset records it)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm

from forecaster.config import settings


def critical_ratio(waste_cost_ratio: float, margin_ratio: float = 0.3) -> float:
    """Newsvendor critical ratio cu/(cu+co): cu = lost margin per unit short, co = cost of a unit
    that expires (wholesale cost ≈ waste_cost_ratio × price)."""
    cu, co = margin_ratio, waste_cost_ratio
    return cu / (cu + co)


def order_quantity(p50: np.ndarray, p80: np.ndarray, ratio: float, on_hand_fresh: np.ndarray | None = None) -> np.ndarray:
    """Quantile implied by the critical ratio, interpolated from P50/P80 (normal-shaped spread),
    minus stock still sellable tomorrow."""
    z80 = norm.ppf(0.8)
    sigma = np.maximum(np.asarray(p80) - np.asarray(p50), 0) / z80
    q = np.asarray(p50) + norm.ppf(np.clip(ratio, 0.05, 0.95)) * sigma
    if on_hand_fresh is not None:
        q = q - np.asarray(on_hand_fresh)
    return np.maximum(q, 0)


def markdown_suggestion(excess_ratio: float, days_to_expiry: int) -> float:
    """Heuristic markdown depth (hackathon-era; replaced by learned elasticity as experiment
    records accumulate). Prices only go down."""
    if excess_ratio <= 0.1:
        return 0.0
    base = 0.2 if excess_ratio < 0.4 else 0.35 if excess_ratio < 0.8 else 0.5
    return min(0.6, base + (0.1 if days_to_expiry <= 1 else 0.0))


@dataclass
class SimResult:
    waste_units: float
    lost_sales_units: float
    sold_units: float
    ordered_units: float


class Shelf:
    """FIFO perishable stock. buckets[i] = units that have been on the shelf i days; a unit is
    sellable on `life` consecutive days and wasted when it would reach age `life`."""

    def __init__(self, life: int):
        self.life = max(int(life), 1)
        self.b = np.zeros(self.life)

    def sellable_next_day(self) -> float:
        """Units still sellable tomorrow — everything except what expires tonight."""
        return float(self.b[: self.life - 1].sum())

    def expiring_next_day(self) -> float:
        """Units that will be on their last sellable day tomorrow."""
        return float(self.b[self.life - 2]) if self.life >= 2 else 0.0

    def step(self, order: float, demand: float) -> tuple[float, float, float]:
        """Age stock, receive the morning order, sell oldest first. Returns (waste, sold, lost)."""
        waste = float(self.b[-1])
        self.b = np.roll(self.b, 1)
        self.b[0] = max(float(order), 0.0)
        remaining = float(demand) if np.isfinite(demand) else 0.0
        sold = 0.0
        for i in range(self.life - 1, -1, -1):
            take = min(self.b[i], remaining)
            self.b[i] -= take
            remaining -= take
            sold += take
        return waste, sold, remaining


def simulate_inventory(demand: np.ndarray, orders: np.ndarray, shelf_life: int) -> SimResult:
    """Fixed order sequence through a FIFO shelf (kept for tests / external order plans)."""
    shelf = Shelf(shelf_life)
    w = s_ = l_ = 0.0
    for d, q in zip(demand, orders):
        a, b, c = shelf.step(q, d)
        w, s_, l_ = w + a, s_ + b, l_ + c
    return SimResult(w, l_, s_, float(np.nansum(orders)))


def simulate_policy(demand: np.ndarray, p50: np.ndarray, p80: np.ndarray, ratio: float,
                    shelf_life: int) -> tuple[SimResult, Shelf]:
    """Each evening, order the critical-ratio quantity for tomorrow NET of stock that will still be
    sellable tomorrow — the same rule the API uses. Returns the result and the final shelf."""
    shelf = Shelf(shelf_life)
    w = s_ = l_ = ordered = 0.0
    for d, a, b in zip(demand, p50, p80):
        q = float(order_quantity(np.array([a]), np.array([b]), ratio,
                                 on_hand_fresh=np.array([shelf.sellable_next_day()]))[0])
        ordered += q
        x, y, z = shelf.step(q, d)
        w, s_, l_ = w + x, s_ + y, l_ + z
    # units still on hand at the end are not counted as waste (horizon cut-off)
    return SimResult(w, l_, s_, ordered), shelf


def compare_policies(df: pd.DataFrame, pred_col: str, p80_col: str, baseline_col: str,
                     baseline_std_col: str = "sales_roll_7_std",
                     waste_cost_ratio: float | None = None) -> pd.DataFrame:
    """Same ordering rule for both policies, so the difference is forecast skill only.
    Baseline P50 = naive forecast; baseline P80 = naive + z80 × trailing 7-day std."""
    wcr = settings.default_waste_cost_ratio if waste_cost_ratio is None else waste_cost_ratio
    ratio = critical_ratio(wcr)
    z80 = norm.ppf(0.8)
    rows = []
    for (store, product), g in df.sort_values("date").groupby(["store_id", "product_id"], observed=True):
        cat = str(g["category"].iloc[0])
        life = settings.shelf_life_days.get(cat, 3)
        demand = g["sales"].to_numpy()
        base50 = g[baseline_col].fillna(g[pred_col]).to_numpy()
        base80 = base50 + z80 * g[baseline_std_col].fillna(0).to_numpy()
        m, _ = simulate_policy(demand, g[pred_col].to_numpy(), g[p80_col].to_numpy(), ratio, life)
        b, _ = simulate_policy(demand, base50, base80, ratio, life)
        rows.append({"store_id": store, "product_id": product, "category": cat,
                     "model_waste": m.waste_units, "baseline_waste": b.waste_units,
                     "model_lost": m.lost_sales_units, "baseline_lost": b.lost_sales_units,
                     "model_sold": m.sold_units, "baseline_sold": b.sold_units,
                     "model_ordered": m.ordered_units, "baseline_ordered": b.ordered_units,
                     "price": float(g["sell_price_main"].median())})
    return pd.DataFrame(rows)
