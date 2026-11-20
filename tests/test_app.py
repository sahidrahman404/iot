import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.db import Database, Reading, utcnow
from app.main import create_app, render_dashboard


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("DB_PATH", ":memory:")
    monkeypatch.setenv("DEVICE_API_KEY", "test-key")
    return load_settings()


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def test_rejects_bad_key(client):
    r = client.post("/api/readings", json={"device_id": "x"}, headers={"X-API-Key": "nope"})
    assert r.status_code == 401


def test_validates_ranges(client):
    r = client.post(
        "/api/readings",
        json={"device_id": "x", "soil_pct": 150},
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 422


def test_post_then_dashboard(client):
    r = client.post(
        "/api/readings",
        json={"device_id": "esp32-abc", "soil_pct": 12.5, "soil_raw": 2800, "air_raw": 2900, "rssi": -60},
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 200
    assert r.json() == {"interval": 60, "soil_status": "dry", "air_level": "poor"}

    page = client.get("/").text
    assert 'id="dashboard"' in page
    assert "Dry, water now" in page
    assert "esp32-abc" in page
    assert "@get(&#39;/stream&#39;" in page or "@get('/stream'" in page


def test_warming_up_when_air_missing(client):
    r = client.post(
        "/api/readings",
        json={"device_id": "x", "soil_pct": 50},
        headers={"X-API-Key": "test-key"},
    )
    assert r.json()["air_level"] == "warming"


def test_history_and_chart_gaps(settings):
    db = Database(":memory:")
    now = utcnow()
    # two runs of readings separated by a 3-hour outage -> two line segments
    for minutes in [*range(300, 240, -1), *range(60, 0, -1)]:
        db.insert(Reading(now - dt.timedelta(minutes=minutes), "x", 2000, 50.0, 1000, -60))
    html = str(render_dashboard(db, settings))
    soil_svg = html.split('class="chart soil"')[1].split("</svg>")[0]
    assert soil_svg.count("<polyline") == 2
    assert "No data yet" not in soil_svg
