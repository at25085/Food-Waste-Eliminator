"""Data-quality gate. Suspicious rows are quarantined with a reason, never silently trained on
and never silently dropped."""
from __future__ import annotations

import pandas as pd


def validate_observations(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validate product-store-day observations. Returns (clean, quarantine)."""
    reasons = pd.Series("", index=df.index, dtype="object")

    def flag(mask: pd.Series, reason: str) -> None:
        mask = mask.fillna(False)
        reasons.loc[mask] = reasons.loc[mask].where(reasons.loc[mask] == "",
                                                    reasons.loc[mask] + ";") + reason

    flag(df["date"].isna(), "missing_timestamp")
    flag(df["store_id"].isna() | df["product_id"].isna(), "malformed_id")
    flag(df["sales"] < 0, "negative_sales")
    if "customer_count" in df:
        flag(df["customer_count"] < 0, "negative_customer_count")
    if "sell_price_main" in df:
        med = df.groupby(["store_id", "product_id"])["sell_price_main"].transform("median")
        flag((df["sell_price_main"] <= 0) | (df["sell_price_main"] > 10 * med), "impossible_price")
    dup = df.duplicated(subset=["store_id", "product_id", "date"], keep="first")
    flag(dup, "duplicate_record")
    if "inventory" in df:
        jump = df.groupby(["store_id", "product_id"])["inventory"].diff().abs()
        scale = df.groupby(["store_id", "product_id"])["inventory"].transform("median").clip(lower=1)
        flag(jump > 20 * scale, "extreme_inventory_jump")

    bad = reasons != ""
    quarantine = df.loc[bad].copy()
    quarantine["reason"] = reasons[bad]
    return df.loc[~bad].copy(), quarantine


def validate_traffic(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validate first-party traffic rows (store_id, date/timestamp, customers_entered, …)."""
    reasons = pd.Series("", index=df.index, dtype="object")

    def flag(mask: pd.Series, reason: str) -> None:
        mask = mask.fillna(False)
        reasons.loc[mask] = reasons.loc[mask].where(reasons.loc[mask] == "",
                                                    reasons.loc[mask] + ";") + reason

    for col, reason in [("customers_entered", "negative_customer_count"),
                        ("transactions", "negative_transactions"),
                        ("units_sold", "negative_units"), ("revenue", "negative_revenue")]:
        if col in df:
            flag(df[col] < 0, reason)
    tcol = "timestamp" if "timestamp" in df else "date"
    flag(df[tcol].isna(), "missing_timestamp")
    flag(df["store_id"].isna(), "malformed_id")
    flag(df.duplicated(subset=["store_id", tcol], keep="first"), "duplicate_record")
    bad = reasons != ""
    q = df.loc[bad].copy()
    q["reason"] = reasons[bad]
    return df.loc[~bad].copy(), q
