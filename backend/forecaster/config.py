"""Central settings. Everything tunable lives here so policy is explicit and reviewable."""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Blank lines in .env (KEY=) mean "not set", so placeholders fall back to the defaults below.
    # FORECASTER_NO_DOTENV=1 (tests) ignores .env files so no real keys or databases are ever used.
    model_config = SettingsConfigDict(
        env_file=() if os.environ.get("FORECASTER_NO_DOTENV") else (REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        extra="ignore", env_ignore_empty=True)

    app_display_name: str = "Freshora"

    data_dir: Path = REPO_ROOT / "data"
    artifacts_dir: Path = REPO_ROOT / "artifacts"
    database_url: str = f"sqlite:///{(REPO_ROOT / 'artifacts' / 'forecaster.db').as_posix()}"

    @field_validator("database_url")
    @classmethod
    def _psycopg_driver(cls, v: str) -> str:
        """Accept the connection string exactly as Tiger Cloud shows it (postgres://…)."""
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    # Write protection: when set, every mutating endpoint requires "Authorization: Bearer <token>"
    api_token: str | None = None
    # Retrain automatically after an upload once a store has enough new days (off in tests).
    auto_retrain: bool = True
    max_upload_bytes: int = 5 * 1024 * 1024

    # Stage-1 traffic forecast as a demand-model input. Measured (architecture_study.json): removing
    # it improved WAPE 15.24% → 15.10% because recent customer counts are already inputs, so it is
    # off by default; the code path stays for stores where it may help.
    use_traffic_forecast: bool = False

    # Promotion policy (docs/ARCHITECTURE.md §6.4)
    min_new_days_for_retrain: int = 14
    min_global_wape_improvement: float = 0.02  # relative: challenger <= champion * (1 - 0.02)
    max_category_wape_degradation: float = 0.05  # relative, per major category
    overfit_ratio_warning: float = 1.5  # validation WAPE / train WAPE
    drift_wape_multiplier: float = 1.15
    bias_warning_threshold: float = 0.05

    # Inventory simulator / decisions
    shelf_life_days: dict[str, int] = {"Fruit and vegetable": 3, "Bakery": 2, "Meat and fish": 4,
                                       "Dairy products": 10, "Eggs": 21}
    # waste cost per unit as a fraction of price. 0.1 → critical ratio 0.75: the operating point where
    # both policies keep lost sales near 5% (policy_study.json); 0.5 ran the store at ~13% stockouts.
    default_waste_cost_ratio: float = 0.1

    # Open-Meteo
    open_meteo_timeout_s: float = 60.0

    # Optional Backboard persistent memory for the manager assistant
    backboard_api_key: str | None = None
    backboard_base_url: str = "https://app.backboard.io/api"

    # Optional AI integrations (read from backend/.env; the app runs without them)
    gemini_api_key: str | None = None
    # The briefing only rewords computed facts, so a small fast model is enough. Measured on a real
    # store's facts: 3.5-flash-lite 1.5-1.9 s; 3.5-flash 12 s, and 503 "high demand" at times.
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_fallback_model: str = "gemini-2.5-flash"  # tried if the first fails; then the template
    gemini_timeout_s: float = 20.0


settings = Settings()

# Rohlik warehouses → coordinates for Open-Meteo joins.
STORE_LOCATIONS: dict[str, dict] = {
    "Prague_1": {"city": "Prague", "lat": 50.0755, "lon": 14.4378, "tz": "Europe/Prague", "country": "CZ", "subdiv": None},
    "Prague_2": {"city": "Prague", "lat": 50.0755, "lon": 14.4378, "tz": "Europe/Prague", "country": "CZ", "subdiv": None},
    "Prague_3": {"city": "Prague", "lat": 50.0755, "lon": 14.4378, "tz": "Europe/Prague", "country": "CZ", "subdiv": None},
    "Brno_1": {"city": "Brno", "lat": 49.1951, "lon": 16.6068, "tz": "Europe/Prague", "country": "CZ", "subdiv": None},
    "Budapest_1": {"city": "Budapest", "lat": 47.4979, "lon": 19.0402, "tz": "Europe/Budapest", "country": "HU", "subdiv": None},
    "Munich_1": {"city": "Munich", "lat": 48.1351, "lon": 11.5820, "tz": "Europe/Berlin", "country": "DE", "subdiv": "BY"},
    "Frankfurt_1": {"city": "Frankfurt", "lat": 50.1109, "lon": 8.6821, "tz": "Europe/Berlin", "country": "DE", "subdiv": "HE"},
}
