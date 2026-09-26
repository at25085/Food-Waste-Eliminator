"""Social-good impact of avoided waste, and the surplus ladder for tomorrow's plan.

Every factor below is cited and shown in the UI. Rohlik's `sales` are "pieces or kg" depending on
the product, so converting units to weight needs an ASSUMED average unit weight per category —
labeled as an assumption wherever it is used.

Surplus ladder, following the EPA Wasted Food Scale (successor to the Food Recovery Hierarchy),
whose top tiers are "prevent wasted food" then "donate":
    prevent (order the right amount) → markdown (sell it) → donate what a markdown will not clear
"""
from __future__ import annotations

# WRAP, "Household food and drink waste in the United Kingdom 2021-22": 6.0 Mt wasted,
# ≈16 Mt CO2e → 2.67 kg CO2e per kg of wasted food (life-cycle; retail factors differ).
CO2E_KG_PER_KG = 16.0 / 6.0
CO2E_SOURCE = "https://www.wrap.ngo/resources/report/household-food-and-drink-waste-united-kingdom-2021-22"

# ReFED, "The Problem" (2024): 29% of 240 M tons of US food unsold or uneaten ≈ 114 B meals
# → 69.6 M tons / 114 B meals ≈ 1.22 lb (0.555 kg) of food per meal.
KG_PER_MEAL = (0.29 * 240e6 * 907.185) / 114e9
MEALS_SOURCE = "https://refed.org/food-waste/the-problem/"

HIERARCHY_SOURCE = "https://www.epa.gov/sustainable-management-food/food-recovery-hierarchy"  # EPA Wasted Food Scale

# ASSUMPTION (not in the data): average weight of one sales unit, by category.
ASSUMED_KG_PER_UNIT = {"Fruit and vegetable": 0.4, "Bakery": 0.1, "Meat and fish": 0.4}


def units_to_impact(units_by_category: dict[str, float]) -> dict:
    kg = sum(u * ASSUMED_KG_PER_UNIT.get(c, 0.25) for c, u in units_by_category.items())
    return {"units": sum(units_by_category.values()), "kg_assumed": kg,
            "co2e_kg": kg * CO2E_KG_PER_KG, "meals": kg / KG_PER_MEAL}


def sources() -> dict:
    return {
        "co2e_kg_per_kg": round(CO2E_KG_PER_KG, 3), "co2e_source": CO2E_SOURCE,
        "kg_per_meal": round(KG_PER_MEAL, 3), "meals_source": MEALS_SOURCE,
        "hierarchy_source": HIERARCHY_SOURCE,
        "assumed_kg_per_unit": ASSUMED_KG_PER_UNIT,
        "caveat": "Units are pieces or kg depending on product (Rohlik); unit weights are assumptions. "
                  "Waste itself is simulated (FIFO shelf-life simulator).",
    }


def ladder(expiring_leftover: float, p50: float, p50_at_markdown: float | None, markdown: float) -> dict:
    """What happens to tomorrow's last-day stock. FIFO: last-day units sell first."""
    if expiring_leftover <= 0:
        return {"action": "sell", "donate_units": 0.0}
    if markdown > 0 and p50_at_markdown is not None:
        extra_demand = max(p50_at_markdown - p50, 0.0)
        remaining = max(expiring_leftover - extra_demand, 0.0)
        return {"action": "markdown" if remaining <= 0 else "markdown_then_donate", "donate_units": remaining}
    return {"action": "donate", "donate_units": expiring_leftover}
