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
    assert item["waste_risk"] == "high" and item["markdown"] > 0  # 28 expiring vs forecast 20
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
