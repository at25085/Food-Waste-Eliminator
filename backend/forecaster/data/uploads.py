"""Owner uploads: a store's own sheet of what it received, sold and threw away.

Every upload is validated before anything can learn from it. Accepted rows become first-party
observations (append-only; a (store, product, date) already stored is a duplicate, not an edit),
attach real outcomes to forecasts made for those days, and count toward the store's retraining
threshold. Rejected rows are quarantined with the reason and returned to the owner.
"""
from __future__ import annotations

import io
import uuid
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy import insert, select

from forecaster.db import schema as S

TEMPLATE_COLUMNS = ["date", "product_id", "product_name", "category", "units_received", "units_sold",
                    "units_wasted", "price", "discount", "stock_end", "customers", "shelf_life_days"]
REQUIRED = ["date", "product_id", "units_sold"]
ALIASES = {
    "day": "date", "sku": "product_id", "item_id": "product_id", "product": "product_name",
    "name": "product_name", "received": "units_received", "purchased": "units_received",
    "units_purchased": "units_received", "sold": "units_sold", "sales": "units_sold",
    "wasted": "units_wasted", "waste": "units_wasted", "waste_units": "units_wasted",
    "unit_price": "price", "sell_price": "price", "closing_stock": "stock_end", "stock": "stock_end",
    "shelf_life": "shelf_life_days", "days_until_spoiled": "shelf_life_days", "shelf_days": "shelf_life_days",
    "footfall": "customers", "customer_count": "customers", "transactions": "customers",
}
CATEGORIES = ("Fruit and vegetable", "Bakery", "Meat and fish")
MAX_ROWS = 200_000


def template_csv() -> str:
    example = pd.DataFrame([
        {"date": "2026-09-20", "product_id": "strawberry_1lb", "product_name": "Strawberries 1 lb",
         "category": "Fruit and vegetable", "units_received": 80, "units_sold": 69, "units_wasted": 4,
         "price": 3.99, "discount": 0, "stock_end": 7, "customers": 301, "shelf_life_days": 3},
    ])
    return example[TEMPLATE_COLUMNS].to_csv(index=False)


def read_sheet(raw: bytes, filename: str) -> pd.DataFrame:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        df = pd.read_excel(io.BytesIO(raw))
    else:
        df = pd.read_csv(io.BytesIO(raw))
    df.columns = [ALIASES.get(str(c).strip().lower().replace(" ", "_"), str(c).strip().lower().replace(" ", "_"))
                  for c in df.columns]
    return df


def validate(df: pd.DataFrame, existing_keys: set[tuple[str, str]], today: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (clean, quarantined-with-reason). Flags; never silently drops or repairs."""
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}. Download the template for the format.")
    df = df.copy()
    for c in TEMPLATE_COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df["product_id"] = df["product_id"].astype("string").str.strip()
    for c in ["units_received", "units_sold", "units_wasted", "price", "discount", "stock_end", "customers",
              "shelf_life_days"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    reasons = pd.Series("", index=df.index, dtype="object")

    def flag(mask, reason):
        mask = pd.Series(mask, index=df.index).fillna(False).astype(bool)
        reasons.loc[mask] = reasons.loc[mask].where(reasons.loc[mask] == "", reasons.loc[mask] + ";") + reason

    flag(df["date"].isna(), "missing_or_bad_date")
    flag(df["date"] > pd.Timestamp(today), "future_date")
    flag(df["product_id"].isna() | (df["product_id"] == ""), "malformed_product_id")
    flag(df["units_sold"].isna(), "missing_units_sold")
    for c in ["units_received", "units_sold", "units_wasted", "stock_end", "customers"]:
        flag(df[c] < 0, f"negative_{c}")
    flag(df["price"] <= 0, "impossible_price")
    flag((df["shelf_life_days"] < 1) | (df["shelf_life_days"] > 60), "shelf_life_out_of_range")
    flag((df["discount"] < 0) | (df["discount"] >= 1), "discount_not_a_fraction")
    flag(df["category"].notna() & ~df["category"].isin(CATEGORIES), "unknown_category")
    flag(df.duplicated(subset=["product_id", "date"], keep="first"), "duplicate_in_file")
    key = list(zip(df["product_id"].astype(str), df["date"].dt.strftime("%Y-%m-%d")))
    flag(pd.Series([k in existing_keys for k in key], index=df.index), "already_uploaded")
    # Stock balance: yesterday's closing stock + received − sold − wasted should ≈ today's closing
    # stock. Differences can be real (shrink), but they must be looked at, not trained on.
    s = df.sort_values(["product_id", "date"])
    prev = s.groupby("product_id")["stock_end"].shift(1)
    consecutive = s.groupby("product_id")["date"].diff().dt.days.eq(1)
    expect = prev + s["units_received"].fillna(0) - s["units_sold"] - s["units_wasted"].fillna(0)
    gap = (expect - s["stock_end"]).abs()
    tol = np.maximum(1.0, 0.05 * s["units_received"].fillna(0).abs())
    flag(pd.Series((consecutive & prev.notna() & s["stock_end"].notna() & (gap > tol)).to_numpy(),
                   index=s.index).reindex(df.index), "stock_does_not_balance")
    # Can't throw away more than was on hand: yesterday's closing stock + today's deliveries.
    # Only checkable when yesterday's closing stock is known.
    available = (prev + s["units_received"].fillna(0)).where(consecutive & prev.notna())
    flag(pd.Series((s["units_wasted"] > available).to_numpy(), index=s.index).reindex(df.index),
         "wasted_more_than_available")

    bad = reasons != ""
    q = df.loc[bad].copy()
    q["reason"] = reasons[bad]
    return df.loc[~bad].copy(), q


def ingest(engine, store_id: str, raw: bytes, filename: str, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    df = read_sheet(raw, filename)
    if len(df) > MAX_ROWS:
        raise ValueError(f"too many rows ({len(df)} > {MAX_ROWS}); split the file")
    with engine.connect() as conn:
        existing = {(r.product_id, r.date.strftime("%Y-%m-%d")) for r in conn.execute(
            select(S.observations.c.product_id, S.observations.c.date).where(S.observations.c.store_id == store_id))}
    clean, bad = validate(df, existing, today)
    upload_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        if len(clean):
            conn.execute(insert(S.observations), [dict(
                store_id=store_id, product_id=str(r.product_id), date=r.date.date(),
                units_sold=_f(r.units_sold), price=_f(r.price), availability=None, inventory=_f(r.stock_end),
                waste_units=_f(r.units_wasted), source="upload", ingested_at=now,
                units_received=_f(r.units_received), discount=_f(r.discount),
                product_name=None if pd.isna(r.product_name) else str(r.product_name),
                category=None if pd.isna(r.category) else str(r.category), upload_id=upload_id,
                shelf_life_days=_f(r.shelf_life_days))
                for r in clean.itertuples()])
            daily = clean.dropna(subset=["customers"]).groupby("date")["customers"].max()
            if len(daily):
                seen = {r.timestamp.date() for r in conn.execute(
                    select(S.traffic_observations.c.timestamp).where(S.traffic_observations.c.store_id == store_id))}
                rows = [dict(store_id=store_id, timestamp=d.to_pydatetime().replace(tzinfo=timezone.utc),
                             customers_entered=float(c), source="upload", ingested_at=now)
                        for d, c in daily.items() if d.date() not in seen]
                if rows:
                    conn.execute(insert(S.traffic_observations), rows)
                    conn.execute(S.stores.update().where(S.stores.c.store_id == store_id).values(traffic_connected=True))
        if len(bad):
            conn.execute(insert(S.quarantine), [{
                "table_name": f"upload:{upload_id}", "reason": r["reason"], "ingested_at": now,
                "payload": {k: (None if pd.isna(v) else (v.strftime("%Y-%m-%d") if isinstance(v, pd.Timestamp) else
                                                          (v if isinstance(v, str) else float(v))))
                            for k, v in r.drop(labels=["reason"]).items() if k in TEMPLATE_COLUMNS}}
                for _, r in bad.iterrows()])
    attached = attach_outcomes(engine, store_id, clean, now)
    summary = {
        "reasons": bad["reason"].str.split(";").explode().value_counts().to_dict() if len(bad) else {},
        "real_waste_units": float(clean["units_wasted"].sum()) if len(clean) else 0.0,
        "units_sold": float(clean["units_sold"].sum()) if len(clean) else 0.0,
        "units_received": float(clean["units_received"].sum()) if len(clean) else 0.0,
    }
    with engine.begin() as conn:
        conn.execute(insert(S.uploads).values(
            upload_id=upload_id, store_id=store_id, filename=filename, created_at=now, rows=int(len(df)),
            accepted=int(len(clean)), quarantined=int(len(bad)),
            first_date=clean["date"].min().date() if len(clean) else None,
            last_date=clean["date"].max().date() if len(clean) else None,
            outcomes_attached=attached, evaluated_days=0, summary=summary))
    return {"upload_id": upload_id, "rows": int(len(df)), "accepted": int(len(clean)),
            "quarantined": int(len(bad)), "outcomes_attached": attached, **summary}


def attach_outcomes(engine, store_id: str, clean: pd.DataFrame, now: datetime) -> int:
    """Attach real outcomes to forecasts already made for these store-product-days. Predictions
    are never edited; a prediction that already has an outcome is left alone."""
    if clean.empty:
        return 0
    lo, hi = clean["date"].min().date(), clean["date"].max().date()
    with engine.connect() as conn:
        preds = pd.read_sql(
            select(S.predictions.c.prediction_id, S.predictions.c.product_id, S.predictions.c.forecast_date)
            .where((S.predictions.c.store_id == store_id) & (S.predictions.c.forecast_date >= lo)
                   & (S.predictions.c.forecast_date <= hi)
                   & ~S.predictions.c.prediction_id.in_(select(S.outcomes.c.prediction_id))), conn)
    if preds.empty:
        return 0
    preds["forecast_date"] = pd.to_datetime(preds["forecast_date"])
    m = preds.merge(clean.assign(product_id=clean["product_id"].astype(str)),
                    left_on=["product_id", "forecast_date"], right_on=["product_id", "date"])
    if m.empty:
        return 0
    with engine.begin() as conn:
        conn.execute(insert(S.outcomes), [dict(
            prediction_id=r.prediction_id, actual_units_sold=_f(r.units_sold),
            actual_customer_count=_f(r.customers), actual_inventory_remaining=_f(r.stock_end),
            waste_units=_f(r.units_wasted), observed_at=now) for r in m.itertuples()])
    return int(len(m))


def _f(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


# ── optional batch sheet ──────────────────────────────────────────────────────────────────────
BATCH_COLUMNS = ["batch_id", "product_id", "product_name", "category", "received_date", "quantity",
                 "expiry_date", "sold", "wasted"]
BATCH_REQUIRED = ["batch_id", "product_id", "received_date", "quantity", "expiry_date"]
BATCH_ALIASES = {"lot": "batch_id", "lot_id": "batch_id", "batch": "batch_id", "received": "received_date",
                 "delivered_on": "received_date", "delivery_date": "received_date", "qty": "quantity",
                 "best_by": "expiry_date", "use_by": "expiry_date", "expires": "expiry_date",
                 "sold_before_expiry": "sold", "waste": "wasted", "spoiled": "wasted", "sku": "product_id"}


def batch_template_csv() -> str:
    return pd.DataFrame([{"batch_id": "B-0917-01", "product_id": "strawberry_1lb", "product_name": "Strawberries 1 lb",
                          "category": "Fruit and vegetable", "received_date": "2026-09-17", "quantity": 40,
                          "expiry_date": "2026-09-19", "sold": 25, "wasted": 15}])[BATCH_COLUMNS].to_csv(index=False)


def validate_batches(df: pd.DataFrame, existing: set[str], today: date) -> tuple[pd.DataFrame, pd.DataFrame]:
    """expiry_date = the last day the batch may be sold. A batch still on the shelf may leave
    sold/wasted blank or partial; re-uploading a batch_id is a duplicate, not an edit."""
    df.columns = [BATCH_ALIASES.get(c, c) for c in df.columns]
    missing = [c for c in BATCH_REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"missing required batch columns: {missing}. Download the batch template.")
    df = df.copy()
    for c in BATCH_COLUMNS:
        if c not in df.columns:
            df[c] = np.nan
    for c in ("received_date", "expiry_date"):
        df[c] = pd.to_datetime(df[c], errors="coerce").dt.normalize()
    for c in ("quantity", "sold", "wasted"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["batch_id"] = df["batch_id"].astype("string").str.strip()
    reasons = pd.Series("", index=df.index, dtype="object")

    def flag(mask, reason):
        mask = pd.Series(mask, index=df.index).fillna(False).astype(bool)
        reasons.loc[mask] = reasons.loc[mask].where(reasons.loc[mask] == "", reasons.loc[mask] + ";") + reason

    flag(df["received_date"].isna() | df["expiry_date"].isna(), "missing_or_bad_date")
    flag(df["received_date"] > pd.Timestamp(today), "future_delivery")
    flag(df["expiry_date"] < df["received_date"], "expires_before_delivery")
    flag((df["expiry_date"] - df["received_date"]).dt.days > 60, "shelf_life_out_of_range")
    flag(~(df["quantity"] > 0), "quantity_not_positive")
    flag((df["sold"] < 0) | (df["wasted"] < 0), "negative_sold_or_wasted")
    flag(df["sold"].fillna(0) + df["wasted"].fillna(0) > df["quantity"] + np.maximum(1.0, 0.02 * df["quantity"]),
         "sold_plus_wasted_exceeds_quantity")
    flag(df["category"].notna() & ~df["category"].isin(CATEGORIES), "unknown_category")
    flag(df["batch_id"].isna() | (df["batch_id"] == ""), "missing_batch_id")
    flag(df.duplicated(subset=["batch_id"], keep="first"), "duplicate_in_file")
    flag(df["batch_id"].isin(existing), "already_uploaded")
    bad = reasons != ""
    q = df.loc[bad].copy()
    q["reason"] = reasons[bad]
    return df.loc[~bad].copy(), q


def ingest_batches(engine, store_id: str, raw: bytes, filename: str, today: date | None = None) -> dict:
    today = today or datetime.now(timezone.utc).date()
    name = (filename or "").lower()
    df = pd.read_excel(io.BytesIO(raw)) if name.endswith((".xlsx", ".xlsm", ".xls")) else pd.read_csv(io.BytesIO(raw))
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    with engine.connect() as conn:
        existing = {r[0] for r in conn.execute(select(S.batches.c.batch_id).where(S.batches.c.store_id == store_id))}
    clean, bad = validate_batches(df, existing, today)
    upload_id, now = uuid.uuid4().hex[:12], datetime.now(timezone.utc)
    with engine.begin() as conn:
        if len(clean):
            conn.execute(insert(S.batches), [dict(
                store_id=store_id, batch_id=str(r.batch_id), product_id=str(r.product_id),
                product_name=None if pd.isna(r.product_name) else str(r.product_name),
                category=None if pd.isna(r.category) else str(r.category),
                received_date=r.received_date.date(), expiry_date=r.expiry_date.date(), quantity=float(r.quantity),
                sold=_f(r.sold), wasted=_f(r.wasted), upload_id=upload_id, ingested_at=now) for r in clean.itertuples()])
        if len(bad):
            conn.execute(insert(S.quarantine), [{
                "table_name": f"upload:{upload_id}", "reason": r["reason"], "ingested_at": now,
                "payload": {k: (None if pd.isna(v) else (v.strftime("%Y-%m-%d") if isinstance(v, pd.Timestamp) else
                                                          (v if isinstance(v, str) else float(v))))
                            for k, v in r.drop(labels=["reason"]).items() if k in BATCH_COLUMNS}}
                for _, r in bad.iterrows()])
        conn.execute(insert(S.uploads).values(
            upload_id=upload_id, store_id=store_id, filename=filename, created_at=now, rows=int(len(df)),
            accepted=int(len(clean)), quarantined=int(len(bad)), kind="batch",
            first_date=clean["received_date"].min().date() if len(clean) else None,
            last_date=clean["received_date"].max().date() if len(clean) else None,
            outcomes_attached=0, evaluated_days=0,
            summary={"reasons": bad["reason"].str.split(";").explode().value_counts().to_dict() if len(bad) else {}}))
    return {"upload_id": upload_id, "kind": "batch", "rows": int(len(df)), "accepted": int(len(clean)),
            "quarantined": int(len(bad)),
            "reasons": bad["reason"].str.split(";").explode().value_counts().to_dict() if len(bad) else {}}
