import os
import uuid
from datetime import date, datetime, timezone

import pytest

_TMP = os.path.join(os.environ.get("TMP", "/tmp"), "fc_test_" + uuid.uuid4().hex)
os.makedirs(_TMP, exist_ok=True)
os.environ["ARTIFACTS_DIR"] = _TMP
for _k in ("GEMINI_API_KEY", "ELEVENLABS_API_KEY", "BACKBOARD_API_KEY", "MONGODB_URI", "API_TOKEN"):
    os.environ[_k] = ""  # tests never call external services, whatever backend/.env contains
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(os.environ.get('TMP', '/tmp'), 'fc_test_' + uuid.uuid4().hex + '.db')}"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import insert, select  # noqa: E402

from forecaster.api import app as api  # noqa: E402
from forecaster.db import schema as S  # noqa: E402

client = TestClient(api.app)


@pytest.fixture(scope="module")
def seeded():
    now = datetime.now(timezone.utc)
    with api.engine.begin() as conn:
        for v, status in [("demand_v1", "retired"), ("demand_v2", "champion")]:
            conn.execute(insert(S.model_versions).values(
                version=v, stage="demand", training_start=date(2022, 1, 1), training_end=date(2024, 1, 1),
                feature_schema_version="fs_test", params={}, metrics={"test": {"wape": 0.16}}, artifact_uri="x",
                created_at=now, promotion_status=status, decision_reason="t", context="production"))
            conn.execute(insert(S.champion_history).values(context="production", version=v, effective_at=now, reason="t"))
        conn.execute(insert(S.predictions).values(
            prediction_id="p1", context="production", store_id="Prague_1", product_id="1", category="Bakery",
            prediction_created_at=now, forecast_date=date(2024, 6, 3), horizon=1, predicted_units=74.0,
            p80_units=90.0, weather_forecast={}, expected_customers=None, model_version="demand_v2",
            feature_schema_version="fs_test"))
    return True


def test_outcome_is_attached_and_prediction_untouched(seeded):
    r = client.post("/api/outcomes", json=[{"prediction_id": "p1", "actual_units_sold": 69, "waste_units": 4}])
    assert r.json()["accepted"] == 1
    again = client.post("/api/outcomes", json=[{"prediction_id": "p1", "actual_units_sold": 1}]).json()
    assert again["rejected"][0]["reason"] == "duplicate_outcome"
    with api.engine.connect() as conn:
        p = conn.execute(select(S.predictions).where(S.predictions.c.prediction_id == "p1")).first()
    assert p.predicted_units == 74.0


def test_unknown_or_negative_outcomes_are_quarantined(seeded):
    r = client.post("/api/outcomes", json=[{"prediction_id": "nope", "actual_units_sold": 1}]).json()
    assert r["rejected"][0]["reason"] == "unknown_prediction"


def test_rollback_repoints_champion_and_keeps_history(seeded):
    assert client.get("/api/model-health").json()["champion"] == "demand_v2"
    r = client.post("/api/models/demand_v1/rollback").json()
    assert r["changed"] and r["previous"] == "demand_v2"
    assert client.get("/api/model-health").json()["champion"] == "demand_v1"
    with api.engine.connect() as conn:
        assert len(conn.execute(select(S.champion_history).where(S.champion_history.c.context == "production")).all()) == 3


def test_traffic_ingestion_validates(seeded):
    r = client.post("/api/traffic", json=[
        {"store_id": "Brno_1", "timestamp": "2026-09-25T10:00:00Z", "customers_entered": 300},
        {"store_id": "Brno_1", "timestamp": "2026-09-25T11:00:00Z", "customers_entered": -5},
    ]).json()
    assert r["accepted"] == 1 and r["quarantined"] == 1


def test_promotion_experiment_lifecycle(seeded):
    bad = client.post("/api/promotions", json={"store_id": "Prague_1", "product_id": "1", "discount": 1.2,
                                               "start_time": "2026-09-26T08:00:00Z", "end_time": "2026-09-26T20:00:00Z",
                                               "inventory_before": 40, "forecast_without_promotion": 20})
    assert bad.status_code == 400
    exp = client.post("/api/promotions", json={"store_id": "Prague_1", "product_id": "1", "discount": 0.3,
                                               "start_time": "2026-09-26T08:00:00Z", "end_time": "2026-09-26T20:00:00Z",
                                               "inventory_before": 40, "forecast_without_promotion": 20}).json()
    out = client.post(f"/api/promotions/{exp['experiment_id']}/outcome",
                      json={"actual_sales": 30, "inventory_after": 10, "waste_after": 2, "revenue": 210}).json()
    assert out["observed_lift"] == pytest.approx(0.5)
    again = client.post(f"/api/promotions/{exp['experiment_id']}/outcome",
                        json={"actual_sales": 1, "inventory_after": 0, "waste_after": 0, "revenue": 1})
    assert again.status_code == 409


def test_data_sources_reports_traffic_state(seeded):
    src = {s["key"]: s for s in client.get("/api/data-sources", params={"store": "Brno_1"}).json()}
    assert src["weather"]["connected"] and not src["events"]["connected"]


def test_manager_notes_are_stored_and_reach_the_briefing_facts(seeded):
    r = client.post("/api/stores/Prague_1/notes", json={"content": "Street festival on Saturday", "kind": "event"})
    assert r.status_code == 200 and r.json()["mirrored_to_backboard"] is False  # no key in tests
    assert client.post("/api/stores/Prague_1/notes", json={"content": " ", "kind": "event"}).status_code == 400
    notes = client.get("/api/stores/Prague_1/notes").json()
    assert notes["notes"][0]["content"] == "Street festival on Saturday"
    from forecaster.memory import relevant_notes
    found, source = relevant_notes(api.engine, "Prague_1", "2024-06-03", "anything")
    assert source == "local" and "Street festival on Saturday" in found


def _write_recs(version: str):
    import pandas as pd
    from forecaster.config import settings
    row = {"store_id": "Prague_1", "product_id": "1", "name": "Rye bread", "category": "Bakery",
           "sell_price_main": 30.0, "p50": 20.0, "p80": 26.0, "expected_customer_count": 8000.0,
           "weather_temperature_max": 21.0, "weather_temperature_min": 9.0, "weather_precipitation_sum": 0.0,
           "weather_code": 3.0, "weather_wind_speed_max": 10.0, "on_hand": 30.0, "expiring_tomorrow": 28.0,
           "shelf_life": 2, "forecast_date": "2024-06-03", "as_of": "2024-06-02", "model_version": version,
           **{f"p50_if_markdown_{m}": 24.0 for m in (20, 30, 35, 45, 50, 60)}}
    pd.DataFrame([row]).to_parquet(settings.artifacts_dir / "recommendations.parquet", index=False)


def test_serving_refuses_a_model_from_another_feature_schema(seeded):
    _write_recs("demand_v2")  # trained on "fs_test"
    assert client.get("/api/stores/Prague_1/recommendations").status_code == 409


def test_briefing_returns_200_when_recommendations_exist(seeded):
    from forecaster.features.build import FEATURE_SCHEMA_VERSION
    now = datetime.now(timezone.utc)
    with api.engine.begin() as conn:
        conn.execute(insert(S.model_versions).values(
            version="demand_cur", stage="demand", training_start=date(2022, 1, 1), training_end=date(2024, 5, 5),
            feature_schema_version=FEATURE_SCHEMA_VERSION, params={}, metrics={}, artifact_uri="x",
            created_at=now, promotion_status="candidate", decision_reason="t", context="test"))
    _write_recs("demand_cur")
    rec = client.get("/api/stores/Prague_1/recommendations").json()
    item = rec["items"][0]
    # 30 on hand, all on their last sellable day (2-day bread), forecast 20; the model says any
    # discount only lifts sales +20%: discounting would give away more on the 20 units that sell
    # anyway than it saves on the other 10 → no discount, donate 10.
    assert item["waste_risk"] == "high" and item["markdown"] == 0
    assert item["surplus_action"] == "donate" and round(item["donate_units"]) == 10
    assert item["order_qty"] < 26  # netted against the 30 units still sellable tomorrow
    b = client.get("/api/stores/Prague_1/briefing", params={"refresh": True})
    assert b.status_code == 200 and b.json()["provider"] == "template" and "Rye bread" in b.json()["text"]


def test_rollback_refuses_a_never_promoted_candidate(seeded):
    now = datetime.now(timezone.utc)
    with api.engine.begin() as conn:
        conn.execute(insert(S.model_versions).values(
            version="demand_rej", stage="demand", training_start=date(2022, 1, 1), training_end=date(2024, 1, 1),
            feature_schema_version="fs_test", params={}, metrics={}, artifact_uri="x", created_at=now,
            promotion_status="rejected", decision_reason="t", context="production"))
    assert client.post("/api/models/demand_rej/rollback").status_code == 409


def test_writes_require_token_when_configured(seeded, monkeypatch):
    monkeypatch.setattr(api.settings, "api_token", "s3cret")
    body = [{"store_id": "Brno_1", "timestamp": "2026-09-25T12:00:00Z", "customers_entered": 1}]
    assert client.post("/api/traffic", json=body).status_code == 401
    assert client.post("/api/traffic", json=body, headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/api/stores").status_code == 200  # reads stay open


def test_resubmitted_traffic_is_quarantined_as_duplicate(seeded):
    body = [{"store_id": "Munich_1", "timestamp": "2026-09-24T09:00:00Z", "customers_entered": 50}]
    assert client.post("/api/traffic", json=body).json()["accepted"] == 1
    again = client.post("/api/traffic", json=body).json()
    assert again["accepted"] == 0 and again["reasons"] == {"duplicate_record": 1}


def _sheet(rows):
    import pandas as pd
    return pd.DataFrame(rows).to_csv(index=False).encode()


def test_upload_validates_rows_and_attaches_real_outcomes(seeded):
    from forecaster.data import uploads
    with api.engine.begin() as conn:
        conn.execute(insert(S.predictions).values(
            prediction_id="p_up", context="production", store_id="Prague_1", product_id="bread",
            category="Bakery", prediction_created_at=datetime.now(timezone.utc), forecast_date=date(2026, 9, 21),
            horizon=1, predicted_units=50.0, p80_units=60.0, weather_forecast={}, expected_customers=None,
            model_version="demand_v2", feature_schema_version="fs_test"))
    good = {"date": "2026-09-20", "product_id": "bread", "category": "Bakery", "units_received": 60,
            "units_sold": 48, "units_wasted": 2, "price": 2.5, "stock_end": 10, "shelf_life_days": 2}
    raw = _sheet([
        good,
        {**good, "date": "2026-09-21", "units_received": 50, "units_sold": 52, "units_wasted": 1, "stock_end": 7},  # 10+50-52-1=7 ✓
        {**good, "date": "2026-09-22", "units_received": 40, "units_sold": 30, "units_wasted": 0, "stock_end": 99},  # doesn't balance
        {**good, "date": "2026-09-23", "units_sold": -1},                     # negative
        {**good, "date": "2099-01-01"},                                       # future
        {**good, "product_id": "milk", "category": "Dairy"},                  # unknown category
        {**good, "product_id": "kale", "units_wasted": 500, "date": "2026-09-21"},  # first day for kale: can't check
    ])
    out = uploads.ingest(api.engine, "Prague_1", raw, "week.csv", today=date(2026, 9, 25))
    assert out["accepted"] == 3 and out["quarantined"] == 4
    assert set(out["reasons"]) == {"stock_does_not_balance", "negative_units_sold", "future_date", "unknown_category"}
    assert out["outcomes_attached"] == 1  # the forecast for bread on 09-21 now has its real outcome
    again = uploads.ingest(api.engine, "Prague_1", raw, "week.csv", today=date(2026, 9, 25))
    assert again["accepted"] == 0 and "already_uploaded" in again["reasons"]


def test_upload_rejects_sheet_without_required_columns(seeded):
    from forecaster.data import uploads
    import pytest as _pt
    with _pt.raises(ValueError, match="missing required columns"):
        uploads.ingest(api.engine, "Prague_1", _sheet([{"date": "2026-09-20", "sold": 3}]), "x.csv")


def test_template_download_and_store_creation(seeded):
    t = client.get("/api/uploads/template.csv")
    assert t.status_code == 200 and "shelf_life_days" in t.text.splitlines()[0]
    r = client.post("/api/stores", json={"store_id": "GT Fresh Demo", "city": "Atlanta", "lat": 33.77, "lon": -84.39,
                                         "timezone": "America/New_York", "country": "US", "subdivision": "GA"})
    assert r.json()["store_id"] == "GT_Fresh_Demo"
    stores = {s["store_id"]: s for s in client.get("/api/stores").json()}
    assert stores["GT_Fresh_Demo"]["label"] == "Demo store — team-authored data"


def test_batch_sheet_validation_shelf_life_and_real_expiry(seeded):
    import pandas as pd
    from forecaster.data import uploads
    from forecaster.pipeline import store_learning
    rows = [
        {"batch_id": "B1", "product_id": "berry", "received_date": "2026-09-14", "quantity": 40, "expiry_date": "2026-09-16", "sold": 40, "wasted": 0},
        {"batch_id": "B2", "product_id": "berry", "received_date": "2026-09-16", "quantity": 40, "expiry_date": "2026-09-18", "sold": 25, "wasted": 15},
        {"batch_id": "B3", "product_id": "berry", "received_date": "2026-09-24", "quantity": 30, "expiry_date": "2026-09-26", "sold": 10},  # still on shelf
        {"batch_id": "B4", "product_id": "berry", "received_date": "2026-09-20", "quantity": 10, "expiry_date": "2026-09-19"},  # expires before delivery
        {"batch_id": "B5", "product_id": "berry", "received_date": "2026-09-20", "quantity": 10, "expiry_date": "2026-09-22", "sold": 9, "wasted": 5},  # > qty
        {"batch_id": "B1", "product_id": "berry", "received_date": "2026-09-14", "quantity": 40, "expiry_date": "2026-09-16"},  # dup
    ]
    out = uploads.ingest_batches(api.engine, "Brno_1", _sheet(rows), "b.csv", today=date(2026, 9, 25))
    assert out["accepted"] == 3 and set(out["reasons"]) == {"expires_before_delivery", "sold_plus_wasted_exceeds_quantity", "duplicate_in_file"}
    b = store_learning.batches(api.engine, "Brno_1")
    assert store_learning.learned_shelf_lives(b) == {"berry": 3}  # Mon→Wed = 3 sellable days
    cohorts = store_learning.open_batch_cohorts(b, pd.Timestamp("2026-09-25"))
    assert cohorts == {"berry": [(20.0, 2)]}  # 30 − 10 sold left, sellable 09-25 and 09-26


def test_adherence_attributes_waste_to_over_ordering(seeded):
    from forecaster.data import uploads
    from forecaster.pipeline import store_learning
    with api.engine.begin() as conn:
        conn.execute(insert(S.predictions).values(
            prediction_id="p_adh", context="production", store_id="Budapest_1", product_id="roll", category="Bakery",
            prediction_created_at=datetime.now(timezone.utc), forecast_date=date(2026, 9, 21), horizon=1,
            predicted_units=40.0, p80_units=48.0, weather_forecast={}, expected_customers=None,
            model_version="demand_v2", feature_schema_version="fs_test", recommended_order=40.0))
    base = {"product_id": "roll", "category": "Bakery", "price": 1.0, "shelf_life_days": 2}
    uploads.ingest(api.engine, "Budapest_1", _sheet([
        {**base, "date": "2026-09-21", "units_received": 70, "units_sold": 45, "units_wasted": 0},  # ordered 30 over plan
        {**base, "date": "2026-09-22", "units_received": 30, "units_sold": 40, "units_wasted": 12},
    ]), "d.csv", today=date(2026, 9, 25))
    a = store_learning.adherence(api.engine, "Budapest_1")
    assert a["compared_orders"] == 1 and a["over_ordered"] == 1 and a["excess_units"] == 30
    assert a["waste_after_over_ordering"] == 12  # the 12 wasted next day trace to the over-order
