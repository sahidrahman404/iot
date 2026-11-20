"""Settings, read from environment variables (mise loads them from .env)."""

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    db_path: Path
    device_api_key: str
    timezone: ZoneInfo
    # How often the ESP32 should post (seconds). Sent back to the device in every response.
    report_interval: int
    # Soil moisture % thresholds (percent is computed on the device from its calibration).
    soil_dry_below: int
    soil_wet_above: int
    # MQ-135 raw ADC thresholds (0-4095). Calibrate these against your own clean-air baseline.
    air_moderate_above: int
    air_poor_above: int
    # How many hours of history the dashboard charts show.
    chart_hours: int


def load_settings() -> Settings:
    return Settings(
        db_path=Path(os.environ.get("DB_PATH", "data/iot.duckdb")),
        device_api_key=os.environ.get("DEVICE_API_KEY", "change-me"),
        timezone=ZoneInfo(os.environ.get("TZ_NAME", "Asia/Jakarta")),
        report_interval=_int("REPORT_INTERVAL", 60),
        soil_dry_below=_int("SOIL_DRY_BELOW", 30),
        soil_wet_above=_int("SOIL_WET_ABOVE", 80),
        air_moderate_above=_int("AIR_MODERATE_ABOVE", 1500),
        air_poor_above=_int("AIR_POOR_ABOVE", 2500),
        chart_hours=_int("CHART_HOURS", 24),
    )
