"""Database schema (SQLAlchemy Core). PostgreSQL + TimescaleDB in production, SQLite in tests.

Predictions and outcomes are separate append-only tables joined by prediction_id, so what the
system believed before reality is never overwritten.
"""
from __future__ import annotations

import logging

from sqlalchemy import (JSON, Boolean, Column, Date, DateTime, Float, ForeignKey, Integer,
                        MetaData, String, Table, Text, create_engine, event, text)
from sqlalchemy.engine import Engine

from forecaster.config import settings

metadata = MetaData()

stores = Table(
    "stores", metadata,
    Column("store_id", String, primary_key=True),
    Column("city", String), Column("lat", Float), Column("lon", Float), Column("timezone", String),
    Column("traffic_connected", Boolean, nullable=False, default=False),
)

model_versions = Table(
    "model_versions", metadata,
    Column("version", String, primary_key=True),
    Column("stage", String, nullable=False),  # demand | traffic
    Column("training_start", Date), Column("training_end", Date),
    Column("feature_schema_version", String, nullable=False),
    Column("params", JSON), Column("metrics", JSON),
    Column("artifact_uri", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("promotion_status", String, nullable=False),  # candidate|champion|rejected|retired
    Column("decision_reason", Text),
    Column("context", String, nullable=False, default="production"),  # production | replay
)

champion_history = Table(
    "champion_history", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("context", String, nullable=False),
    Column("version", String, ForeignKey("model_versions.version"), nullable=False),
    Column("effective_at", DateTime(timezone=True), nullable=False),
    Column("as_of_date", Date),  # simulated business date for replay context
    Column("reason", Text),
)

predictions = Table(
    "predictions", metadata,
    Column("prediction_id", String, primary_key=True),
    Column("context", String, nullable=False),  # production | replay
    Column("store_id", String, nullable=False), Column("product_id", String, nullable=False),
    Column("category", String),
    Column("prediction_created_at", DateTime(timezone=True), nullable=False),
    Column("forecast_date", Date, nullable=False), Column("horizon", Integer, nullable=False),
    Column("predicted_units", Float, nullable=False), Column("p80_units", Float),
    Column("weather_forecast", JSON), Column("expected_customers", Float),
    Column("model_version", String, nullable=False),
    Column("feature_schema_version", String, nullable=False),
)

outcomes = Table(
    "outcomes", metadata,
    Column("prediction_id", String, ForeignKey("predictions.prediction_id"), primary_key=True),
    Column("actual_units_sold", Float), Column("actual_customer_count", Float),
    Column("actual_inventory_remaining", Float), Column("waste_units", Float),
    Column("observed_at", DateTime(timezone=True), nullable=False),
)

traffic_observations = Table(  # natural key → eligible for a Timescale hypertable
    "traffic_observations", metadata,
    Column("store_id", String, primary_key=True), Column("timestamp", DateTime(timezone=True), primary_key=True),
    Column("customers_entered", Float), Column("transactions", Float),
    Column("units_sold", Float), Column("revenue", Float),
    Column("source", String), Column("ingested_at", DateTime(timezone=True), nullable=False),
)

observations = Table(  # natural key → eligible for a Timescale hypertable
    "observations", metadata,
    Column("store_id", String, primary_key=True), Column("product_id", String, primary_key=True),
    Column("date", Date, primary_key=True),
    Column("units_sold", Float), Column("price", Float), Column("availability", Float),
    Column("inventory", Float), Column("waste_units", Float),
    Column("source", String), Column("ingested_at", DateTime(timezone=True), nullable=False),
)

quarantine = Table(
    "quarantine", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("table_name", String, nullable=False), Column("payload", JSON, nullable=False),
    Column("reason", String, nullable=False), Column("ingested_at", DateTime(timezone=True), nullable=False),
)

anomaly_days = Table(
    "anomaly_days", metadata,
    Column("store_id", String, primary_key=True), Column("date", Date, primary_key=True),
    Column("is_anomaly", Boolean, nullable=False), Column("reason", Text),
    Column("training_policy", String, nullable=False, default="feature"),  # exclude|feature|keep
)

promotion_experiments = Table(
    "promotion_experiments", metadata,
    Column("experiment_id", String, primary_key=True),
    Column("store_id", String), Column("product_id", String),
    Column("discount", Float), Column("start_time", DateTime(timezone=True)),
    Column("end_time", DateTime(timezone=True)),
    Column("inventory_before", Float), Column("forecast_without_promotion", Float),
    Column("actual_sales", Float), Column("inventory_after", Float),
    Column("waste_after", Float), Column("revenue", Float),
)

manager_notes = Table(
    "manager_notes", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("store_id", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("kind", String, nullable=False),  # event | preference | override
    Column("applies_on", Date),  # optional: the business date the note is about
    Column("content", Text, nullable=False),
    Column("backboard_memory_id", String),
)

retrain_runs = Table(
    "retrain_runs", metadata,
    Column("run_id", String, primary_key=True),
    Column("context", String, nullable=False),
    Column("as_of_date", Date), Column("trigger", String),
    Column("champion_version", String), Column("candidate_version", String),
    Column("new_observation_days", Integer),
    Column("comparison", JSON), Column("decision", String), Column("reason", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

HYPERTABLES = {"predictions": "forecast_date", "traffic_observations": "timestamp", "observations": "date"}
log = logging.getLogger(__name__)
# Filled by create_all: which tables really became hypertables and why the others did not.
TIMESCALE_STATUS: dict = {"extension": False, "hypertables": [], "not_converted": {}}


def get_engine(url: str | None = None) -> Engine:
    url = url or settings.database_url
    if url.startswith("sqlite"):
        settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
        return engine
    return create_engine(url, pool_pre_ping=True)


def create_all(engine: Engine) -> None:
    metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        with engine.begin() as conn:
            has_ts = conn.execute(text(
                "SELECT count(*) FROM pg_available_extensions WHERE name='timescaledb'")).scalar()
            if has_ts:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
                TIMESCALE_STATUS["extension"] = True
                # Hypertables need the time column in every unique index; ours use surrogate keys,
                # so we convert with migrate_data and let Timescale warn if a constraint blocks it.
                for table, col in HYPERTABLES.items():
                    sp = conn.begin_nested()  # a failed attempt must not abort the outer transaction
                    try:
                        conn.execute(text(
                            f"SELECT create_hypertable('{table}', by_range('{col}'), "
                            f"if_not_exists => TRUE, migrate_data => TRUE)"))
                        sp.commit()
                        TIMESCALE_STATUS["hypertables"].append(table)
                    except Exception as e:  # noqa: BLE001 — unique keys without the time column block conversion
                        sp.rollback()
                        TIMESCALE_STATUS["not_converted"][table] = str(e).splitlines()[0]
                        log.warning("%s stays a plain Postgres table: %s", table, TIMESCALE_STATUS["not_converted"][table])
                existing = conn.execute(text("SELECT hypertable_name FROM timescaledb_information.hypertables")).scalars().all()
                TIMESCALE_STATUS["hypertables"] = sorted(set(existing))
