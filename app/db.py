"""DuckDB storage.

DuckDB allows exactly one read-write process per database file, so the server must
run with a single worker. Inside that process we share one connection and give every
call its own cursor, which makes it safe to use from worker threads
(`asyncio.to_thread`). DuckDB calls are blocking, so never call these functions
directly from an async handler.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import duckdb

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    ts        TIMESTAMP NOT NULL,   -- UTC, naive
    device_id VARCHAR   NOT NULL,
    soil_raw  INTEGER,
    soil_pct  DOUBLE,
    air_raw   INTEGER,              -- NULL while the MQ-135 heater warms up
    rssi      INTEGER
);
"""


@dataclass(frozen=True)
class Reading:
    ts: dt.datetime  # UTC, naive
    device_id: str
    soil_raw: int | None
    soil_pct: float | None
    air_raw: int | None
    rssi: int | None


@dataclass(frozen=True)
class Bucket:
    ts: dt.datetime  # UTC, naive; start of bucket
    soil_pct: float | None
    air_raw: float | None


class Database:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(path))
        self._con.execute(SCHEMA)

    def close(self) -> None:
        self._con.close()

    def insert(self, r: Reading) -> None:
        with self._con.cursor() as cur:
            cur.execute(
                "INSERT INTO readings VALUES (?, ?, ?, ?, ?, ?)",
                [r.ts, r.device_id, r.soil_raw, r.soil_pct, r.air_raw, r.rssi],
            )

    def latest(self) -> Reading | None:
        with self._con.cursor() as cur:
            row = cur.execute(
                "SELECT ts, device_id, soil_raw, soil_pct, air_raw, rssi "
                "FROM readings ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        return Reading(*row) if row else None

    def history(self, hours: int, buckets: int = 96) -> list[Bucket]:
        """Averages over `hours`, split into roughly `buckets` time buckets."""
        bucket_minutes = max(1, hours * 60 // buckets)
        with self._con.cursor() as cur:
            rows = cur.execute(
                """
                SELECT time_bucket(to_minutes(?::INTEGER), ts) AS b,
                       avg(soil_pct), avg(air_raw)
                FROM readings
                WHERE ts >= ?
                GROUP BY b
                ORDER BY b
                """,
                [bucket_minutes, utcnow() - dt.timedelta(hours=hours)],
            ).fetchall()
        return [Bucket(*row) for row in rows]

    def delete_older_than(self, days: int) -> None:
        with self._con.cursor() as cur:
            cur.execute("DELETE FROM readings WHERE ts < ?", [utcnow() - dt.timedelta(days=days)])


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)
