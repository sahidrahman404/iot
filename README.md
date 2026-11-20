# web-iot: Garden Monitor

An ESP32 with a soil moisture sensor, an MQ-135 air sensor and an OLED screen, reporting to a live web dashboard.

```
ESP32 ──POST /api/readings (JSON, every 60 s)──▶ FastAPI ──▶ DuckDB (data/iot.duckdb)
                                                   │
Browser ◀── Datastar SSE (GET /stream) ────────────┘  re-renders #dashboard on each reading
```

| Part | Stack |
|---|---|
| Toolchain | mise (Python 3.13, uv, PlatformIO) |
| Server | FastAPI + uvicorn, Jinja2 templates |
| UI | Datastar 1.0.5 (self-hosted at `app/static/datastar.js`) and server-rendered SVG charts |
| Database | DuckDB ≥ 2.0.1 |
| Firmware | PlatformIO, Arduino framework (Adafruit SSD1306, ArduinoJson, WiFiManager) |

## Quick start (no hardware needed)

```bash
mise install                 # python, uv, platformio
uv sync                      # python deps -> .venv
cp .env.example .env         # then set DEVICE_API_KEY to a long random string
mise run dev                 # http://localhost:8000
mise run fake-device         # in a second terminal: posts fake readings every 5 s
mise run test
```

## Wiring

| Part | Part pin | ESP32 pin |
|---|---|---|
| OLED SSD1306 (I2C) | VCC / GND | 3V3 / GND |
| | SDA / SCL | GPIO 21 / GPIO 22 |
| Soil sensor (capacitive) | VCC / GND | 3V3 / GND |
| | AOUT | **GPIO 34** |
| MQ-135 module | VCC / GND | **VIN (5 V)** / GND |
| | AO | **GPIO 35, through a voltage divider** |

**MQ-135 voltage divider.** The MQ-135 output can reach 5 V, but the ESP32 accepts at most 3.3 V:

```
MQ-135 AO ──[ 10 kΩ ]──┬── GPIO 35
                       │
                    [ 20 kΩ ]
                       │
                      GND
```

Use ADC1 pins (GPIO 32–39) for analog sensors. ADC2 pins don't work while WiFi is on.

## Flashing the ESP32

```bash
mise run fw:upload           # compiles and flashes over USB (needs a *data* USB cable)
mise run fw:monitor          # serial log: raw readings, POST results
```

On first boot the ESP32 opens a hotspot named **GardenMonitor-Setup**:

1. Connect to it with your phone.
2. Choose your WiFi network.
3. Enter the server URL, for example `http://192.168.1.10:8000` (your computer's LAN IP, not `localhost`).
4. Enter the API key, which is `DEVICE_API_KEY` from `.env`.

To run setup again, power on the board and hold **BOOT** within 3 s.

If the upload can't find the board on Linux, add yourself to the `dialout` group and log in again:

```bash
sudo usermod -aG dialout $USER
```

## Calibration

- **Soil.** In `mise run fw:monitor`, note the `soil raw` value with the sensor in dry air and again in a glass of water. Put those numbers in `SOIL_RAW_DRY` and `SOIL_RAW_WET` in `firmware/src/main.cpp`, then reflash.
- **Air.** The MQ-135 only gives a *relative* reading, not real ppm.
  1. Give a new sensor 24–48 h of burn-in.
  2. Note its raw value in clean air.
  3. Set `AIR_MODERATE_ABOVE` and `AIR_POOR_ABOVE` in `.env` above that baseline.
  4. Restart the server. You don't need to reflash.

The soil and air thresholds live on the server, and the device shows the labels the server sends back. Changing a threshold only takes an edit to `.env` and a server restart.

## Things to know

- **Run one server process only.** DuckDB allows a single writing process per file, and live updates use an in-process broadcaster. `mise run serve` already uses `--workers 1`.
- **Don't open `data/iot.duckdb` with the DuckDB CLI while the server is running.** Stop the server first, or open a copy of the file.
- **Older tools can't read the database.** DuckDB 2.0 writes storage format v69, which older DuckDB tools and drivers can't open.
- **Retention.** Readings older than 365 days are deleted once a day (`RETENTION_DAYS` in `app/main.py`).
- **FastAPI and datastar-py.** Recent FastAPI versions stream async-generator endpoints as JSON lines by themselves. That's why `/stream` returns `DatastarResponse(...)` explicitly instead of using datastar-py's `@datastar_response` decorator.

## Layout

```
app/
  main.py        routes: POST /api/readings, GET /, GET /stream, GET /healthz
  db.py          DuckDB schema + queries
  broker.py      in-process pub/sub that wakes dashboard streams
  charts.py      SVG line charts
  config.py      settings from env
  templates/     index.html, _dashboard.html (the morph target)
  static/        datastar.js, style.css
firmware/        PlatformIO project (src/main.cpp)
tools/           fake_device.py
tests/
```
