"""FastAPI app: device ingest API + Datastar-driven live dashboard."""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Literal

from datastar_py import ServerSentEventGenerator as SSE
from datastar_py.fastapi import DatastarResponse
from datastar_py.sse import DatastarEvent
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from pydantic import BaseModel, Field

from .broker import Broker
from .charts import line_chart
from .config import Settings, load_settings
from .db import Database, Reading, utcnow

log = logging.getLogger("web_iot")
HERE = Path(__file__).parent
RETENTION_DAYS = 365
KEEPALIVE_SECONDS = 15  # also refreshes the "last seen" text and online/offline state

templates = Environment(
    loader=FileSystemLoader(HERE / "templates"),
    autoescape=select_autoescape(["html"]),
)


# ---------------------------------------------------------------- device API models


class ReadingIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    soil_raw: int | None = Field(default=None, ge=0, le=4095)
    soil_pct: float | None = Field(default=None, ge=0, le=100)
    air_raw: int | None = Field(default=None, ge=0, le=4095)  # None while warming up
    rssi: int | None = Field(default=None, ge=-127, le=0)


SoilStatus = Literal["dry", "ok", "wet", "unknown"]
AirLevel = Literal["good", "moderate", "poor", "warming", "unknown"]


class DeviceResponse(BaseModel):
    """Sent back to the ESP32 after every post: settings plus anything it should display."""

    interval: int
    soil_status: SoilStatus
    air_level: AirLevel


# ---------------------------------------------------------------- classification


def soil_status(s: Settings, pct: float | None) -> SoilStatus:
    if pct is None:
        return "unknown"
    if pct < s.soil_dry_below:
        return "dry"
    if pct > s.soil_wet_above:
        return "wet"
    return "ok"


def air_level(s: Settings, raw: float | None, *, online: bool = True) -> AirLevel:
    if raw is None:
        return "warming" if online else "unknown"
    if raw > s.air_poor_above:
        return "poor"
    if raw > s.air_moderate_above:
        return "moderate"
    return "good"


def ago(then: dt.datetime, now: dt.datetime) -> str:
    secs = int((now - then).total_seconds())
    if secs < 60:
        return f"{max(secs, 0)} s ago"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} d ago"


# ---------------------------------------------------------------- dashboard rendering


def build_dashboard(db: Database, s: Settings) -> dict:
    """Gather everything the dashboard partial needs. Blocking (DuckDB): run in a thread."""
    now = utcnow()
    latest = db.latest()
    history = db.history(s.chart_hours)
    start = now - dt.timedelta(hours=s.chart_hours)
    bucket = dt.timedelta(minutes=max(1, s.chart_hours * 60 // 96))
    max_gap = max(bucket * 2, dt.timedelta(seconds=s.report_interval * 3))

    online = latest is not None and (now - latest.ts).total_seconds() < s.report_interval * 3
    common = dict(start=start, end=now, tz=s.timezone, max_gap=max_gap)

    return {
        "latest": latest,
        "online": online,
        "last_seen": ago(latest.ts, now) if latest else None,
        "last_seen_local": (
            latest.ts.replace(tzinfo=dt.UTC).astimezone(s.timezone) if latest else None
        ),
        "soil_status": soil_status(s, latest.soil_pct if latest else None),
        "air_level": air_level(s, latest.air_raw if latest else None, online=online),
        "chart_hours": s.chart_hours,
        "soil_chart": line_chart(
            [(b.ts, b.soil_pct) for b in history],
            y_min=0,
            y_max=100,
            css_class="soil",
            label=f"Soil moisture, last {s.chart_hours} hours",
            thresholds=(s.soil_dry_below, s.soil_wet_above),
            **common,
        ),
        "air_chart": line_chart(
            [(b.ts, b.air_raw) for b in history],
            y_min=0,
            y_max=4095,
            css_class="air",
            label=f"Air quality (MQ-135 raw), last {s.chart_hours} hours",
            thresholds=(s.air_moderate_above, s.air_poor_above),
            **common,
        ),
    }


def render_dashboard(db: Database, s: Settings) -> Markup:
    ctx = build_dashboard(db, s)
    return Markup(templates.get_template("_dashboard.html").render(ctx))


# ---------------------------------------------------------------- app


async def _retention_loop(db: Database) -> None:
    while True:
        try:
            await asyncio.to_thread(db.delete_older_than, RETENTION_DAYS)
        except Exception:
            log.exception("retention cleanup failed")
        await asyncio.sleep(24 * 3600)


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or load_settings()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if s.device_api_key == "change-me":
            log.warning("DEVICE_API_KEY is the default 'change-me' - set it in .env")
        app.state.db = Database(s.db_path)
        app.state.broker = Broker()
        cleanup = asyncio.create_task(_retention_loop(app.state.db))
        try:
            yield
        finally:
            cleanup.cancel()
            app.state.db.close()

    app = FastAPI(title="web-iot", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

    @app.post("/api/readings", response_model=DeviceResponse)
    async def post_reading(
        body: ReadingIn,
        request: Request,
        x_api_key: Annotated[str, Header()] = "",
    ) -> DeviceResponse:
        if not secrets.compare_digest(x_api_key.encode(), s.device_api_key.encode()):
            raise HTTPException(status_code=401, detail="invalid api key")
        reading = Reading(ts=utcnow(), **body.model_dump())
        await asyncio.to_thread(request.app.state.db.insert, reading)
        request.app.state.broker.publish()
        return DeviceResponse(
            interval=s.report_interval,
            soil_status=soil_status(s, body.soil_pct),
            air_level=air_level(s, body.air_raw),
        )

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> str:
        dashboard = await asyncio.to_thread(render_dashboard, request.app.state.db, s)
        return templates.get_template("index.html").render(dashboard=dashboard)

    @app.get("/stream")
    async def stream(request: Request) -> DatastarResponse:
        """Long-lived Datastar stream: re-sends the dashboard whenever a reading arrives.

        Returns DatastarResponse explicitly instead of using datastar-py's
        @datastar_response decorator: FastAPI >= 0.13x unwraps decorated async
        generators and streams them as JSON lines itself, which breaks SSE.
        """
        db: Database = request.app.state.db
        broker: Broker = request.app.state.broker

        async def events() -> AsyncIterator[DatastarEvent]:
            async with broker.subscribe() as wake:
                while True:
                    yield SSE.patch_elements(await asyncio.to_thread(render_dashboard, db, s))
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(wake.get(), timeout=KEEPALIVE_SECONDS)

        return DatastarResponse(events())

    @app.get("/healthz")
    async def healthz(request: Request) -> dict:
        return {"ok": True, "streams": request.app.state.broker.subscriber_count}

    return app


app = create_app()
