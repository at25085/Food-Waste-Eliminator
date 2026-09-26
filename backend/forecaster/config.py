"""Central settings. Everything tunable lives here so policy is explicit and reviewable."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"), extra="ignore")

    app_display_name: str = "WasteLess"  # working name; rename before submission (see docs)

    data_dir: Path = REPO_ROOT / "data"
    artifacts_dir: Path = REPO_ROOT / "artifacts"
    database_url: str = f"sqlite:///{(REPO_ROOT / 'artifacts' / 'forecaster.db').as_posix()}"

    # Write protection: when set, every mutating endpoint requires "Authorization: Bearer <token>"
    api_token: str | None = None
    max_upload_bytes: int = 5 * 1024 * 1024

    # Promotion policy (docs/ARCHITECTURE.md §6.4)
    min_new_days_for_retrain: int = 14
    min_global_wape_improvement: float = 0.02  # relative: challenger <= champion * (1 - 0.02)
    max_category_wape_degradation: float = 0.05  # relative, per major category
    overfit_ratio_warning: float = 1.5  # validation WAPE / train WAPE
    drift_wape_multiplier: float = 1.15
    bias_warning_threshold: float = 0.05

    # Inventory simulator / decisions
    shelf_life_days: dict[str, int] = {"Fruit and vegetable": 3, "Bakery": 2, "Meat and fish": 4}
    # waste cost per unit as a fraction of price. 0.1 → critical ratio 0.75: the operating point where
    # both policies keep lost sales near 5% (policy_study.json); 0.5 ran the store at ~13% stockouts.
    default_waste_cost_ratio: float = 0.1

    # Open-Meteo
    open_meteo_timeout_s: float = 60.0

    # Optional MongoDB Atlas model-card store
    mongodb_uri: str | None = None
    mongodb_db: str = "forecaster"

    # Optional Backboard persistent memory for the manager assistant
    backboard_api_key: str | None = None
    backboard_base_url: str = "https://app.backboard.io/api"

    # Optional AI integrations (read from backend/.env; the app runs without them)
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash"
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"
    elevenlabs_model: str = "eleven_flash_v2_5"


settings = Settings()

# Rohlik warehouses → coordinates for Open-Meteo joins.
STORE_LOCATIONS: dict[str, dict] = {
    "Prague_1": {"city": "Prague", "lat": 50.0755, "lon": 14.4378, "tz": "Europe/Prague"},
    "Prague_2": {"city": "Prague", "lat": 50.0755, "lon": 14.4378, "tz": "Europe/Prague"},
    "Prague_3": {"city": "Prague", "lat": 50.0755, "lon": 14.4378, "tz": "Europe/Prague"},
    "Brno_1": {"city": "Brno", "lat": 49.1951, "lon": 16.6068, "tz": "Europe/Prague"},
    "Budapest_1": {"city": "Budapest", "lat": 47.4979, "lon": 19.0402, "tz": "Europe/Budapest"},
    "Munich_1": {"city": "Munich", "lat": 48.1351, "lon": 11.5820, "tz": "Europe/Berlin"},
    "Frankfurt_1": {"city": "Frankfurt", "lat": 50.1109, "lon": 8.6821, "tz": "Europe/Berlin"},
}
