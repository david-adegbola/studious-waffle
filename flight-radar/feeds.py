"""Live aircraft data sources.

Both airplanes.live and adsb.lol expose the same free, keyless
readsb-style endpoint: /v2/point/{lat}/{lon}/{radius_nm}. We try them in
order and fall back to the next one if a request fails.
"""

from __future__ import annotations

import json
import math
import random
import threading
import time
import urllib.request
from dataclasses import dataclass

SOURCES = [
    ("airplanes.live", "https://api.airplanes.live/v2/point/{lat:.4f}/{lon:.4f}/{radius}"),
    ("adsb.lol", "https://api.adsb.lol/v2/point/{lat:.4f}/{lon:.4f}/{radius}"),
]

USER_AGENT = "pi-flight-radar/1.0 (+https://github.com/david-adegbola/studious-waffle)"


@dataclass
class Aircraft:
    hex: str
    callsign: str
    lat: float
    lon: float
    alt_ft: int | None  # None = unknown, 0 = on the ground
    speed_kt: float | None
    track_deg: float | None
    type_code: str = ""


def _parse(payload: dict) -> list[Aircraft]:
    planes = []
    for ac in payload.get("ac", []) or []:
        lat, lon = ac.get("lat"), ac.get("lon")
        if lat is None or lon is None:
            continue
        alt = ac.get("alt_baro")
        if alt == "ground":
            alt = 0
        elif not isinstance(alt, (int, float)):
            alt = None
        planes.append(Aircraft(
            hex=ac.get("hex", "?"),
            callsign=(ac.get("flight") or ac.get("r") or ac.get("hex", "")).strip().upper(),
            lat=float(lat),
            lon=float(lon),
            alt_ft=int(alt) if alt is not None else None,
            speed_kt=ac.get("gs"),
            track_deg=ac.get("track", ac.get("true_heading")),
            type_code=ac.get("t", "") or "",
        ))
    return planes


class Feed:
    """Polls a live source in a background thread so the UI never blocks."""

    def __init__(self, lat: float, lon: float, radius_nm: float, interval: float = 5.0):
        self.lat, self.lon = lat, lon
        self.radius_nm = radius_nm
        self.interval = interval
        self.aircraft: list[Aircraft] = []
        self.updated_at: float = 0.0  # time.monotonic() of last good fetch
        self.source = ""
        self.error = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def stop(self):
        self._stop.set()

    def snapshot(self):
        with self._lock:
            return list(self.aircraft), self.updated_at, self.source, self.error

    def _fetch(self) -> tuple[list[Aircraft], str]:
        # Ask for a bit more than the display range so planes don't pop in at the edge.
        radius = max(1, min(250, int(math.ceil(self.radius_nm * 1.2))))
        last_err = None
        for name, template in SOURCES:
            url = template.format(lat=self.lat, lon=self.lon, radius=radius)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    return _parse(json.load(resp)), name
            except Exception as exc:  # network down, rate-limited, bad JSON...
                last_err = f"{name}: {exc}"
        raise RuntimeError(last_err or "no sources")

    def _run(self):
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                planes, source = self._fetch()
                with self._lock:
                    self.aircraft, self.source, self.error = planes, source, ""
                    self.updated_at = time.monotonic()
            except Exception as exc:
                with self._lock:
                    self.error = str(exc)[:80]
            self._stop.wait(max(0.5, self.interval - (time.monotonic() - started)))


class DemoFeed(Feed):
    """Simulated traffic for testing without Wi-Fi (python radar.py --demo)."""

    def __init__(self, lat, lon, radius_nm, interval=5.0, count=9):
        super().__init__(lat, lon, radius_nm, interval)
        rng = random.Random(42)
        airlines = ["BAW", "EZY", "RYR", "DLH", "AFR", "KLM", "UAL", "AAL", "VIR", "FIN", "SAS", "QTR"]
        self._sim = []
        for _ in range(count):
            bearing = rng.uniform(0, 2 * math.pi)
            dist_nm = rng.uniform(0.15, 0.95) * radius_nm
            self._sim.append({
                "hex": f"{rng.getrandbits(24):06x}",
                "callsign": f"{rng.choice(airlines)}{rng.randint(10, 9999)}",
                "x": dist_nm * math.sin(bearing),
                "y": dist_nm * math.cos(bearing),
                "alt": rng.choice([0, 2500, 6000, 11000, 24000, 36000, 39000]),
                "gs": rng.uniform(180, 480),
                "trk": rng.uniform(0, 360),
            })
        self._t = time.monotonic()

    def _fetch(self):
        now = time.monotonic()
        dt_h = (now - self._t) / 3600
        self._t = now
        planes = []
        for p in self._sim:
            if p["alt"] == 0:
                p["gs"] = 15
            p["x"] += p["gs"] * dt_h * math.sin(math.radians(p["trk"]))
            p["y"] += p["gs"] * dt_h * math.cos(math.radians(p["trk"]))
            if math.hypot(p["x"], p["y"]) > self.radius_nm * 1.1:  # wrap back in
                p["trk"] = (math.degrees(math.atan2(-p["x"], -p["y"])) + 20) % 360
            lat = self.lat + p["y"] / 60
            lon = self.lon + p["x"] / (60 * math.cos(math.radians(self.lat)))
            planes.append(Aircraft(p["hex"], p["callsign"], lat, lon, p["alt"], p["gs"], p["trk"], "A320"))
        return planes, "demo"
