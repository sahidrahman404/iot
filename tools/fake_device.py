"""Pretend to be the ESP32: post a slowly drifting reading every few seconds.

    mise run fake-device            # every 5 s to http://localhost:8000
    uv run python -m tools.fake_device --url http://192.168.1.10:8000 --every 2
"""

import argparse
import json
import math
import os
import random
import time
import urllib.request


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--every", type=float, default=5.0, help="seconds between posts")
    p.add_argument("--key", default=os.environ.get("DEVICE_API_KEY", "change-me"))
    args = p.parse_args()

    t0 = time.time()
    while True:
        t = time.time() - t0
        soil_pct = 55 + 25 * math.sin(t / 120) + random.uniform(-2, 2)
        soil_pct = min(max(soil_pct, 0), 100)
        body = {
            "device_id": "fake-esp32",
            "soil_pct": round(soil_pct, 1),
            "soil_raw": int(3000 - soil_pct * 18),
            "air_raw": int(1200 + 900 * (1 + math.sin(t / 90)) / 2 + random.uniform(-60, 60)),
            "rssi": random.randint(-75, -50),
        }
        req = urllib.request.Request(
            f"{args.url}/api/readings",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-API-Key": args.key},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                print(body, "->", resp.read().decode())
        except Exception as e:  # keep going if the server restarts
            print("post failed:", e)
        time.sleep(args.every)


if __name__ == "__main__":
    main()
