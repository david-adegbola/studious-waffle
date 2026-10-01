#!/usr/bin/env python3
"""Tiny round flight radar.

Plots nearby aircraft and their callsigns on a round, radar-style display,
refreshed from live ADS-B data over Wi-Fi every 5 seconds.

    python3 radar.py --lat 60.17 --lon 24.94            # small window on your desktop
    python3 radar.py --lat 60.17 --lon 24.94 --fullscreen   # round HAT/screen on the Pi
    python3 radar.py --demo                             # simulated traffic, no network

Keys: +/- or mouse wheel zoom, tap/click cycles range, L toggles labels, Esc/Q quits.
"""

from __future__ import annotations

import argparse
import math
import os
import time

from feeds import DemoFeed, Feed

# Phosphor-green palette
BG = (0, 0, 0)
FACE = (2, 14, 6)
GRID = (0, 70, 30)
GRID_DIM = (0, 40, 18)
SWEEP = (60, 255, 120)
TEXT = (90, 230, 130)
TEXT_DIM = (40, 140, 70)
PLANE = (170, 255, 190)
GROUND = (230, 190, 60)
HOME = (255, 90, 90)
WARN = (255, 120, 80)

RANGES_NM = [5, 10, 15, 25, 40, 60, 100, 150, 250]
SWEEP_PERIOD = 4.0  # seconds per revolution


def parse_args():
    p = argparse.ArgumentParser(description="Tiny round live flight radar")
    p.add_argument("--lat", type=float, default=float(os.environ.get("RADAR_LAT", "51.4700")),
                   help="your latitude (or env RADAR_LAT)")
    p.add_argument("--lon", type=float, default=float(os.environ.get("RADAR_LON", "-0.4543")),
                   help="your longitude (or env RADAR_LON)")
    p.add_argument("--range", type=float, default=float(os.environ.get("RADAR_RANGE_NM", "25")),
                   help="display radius in nautical miles (default 25)")
    p.add_argument("--size", type=int, default=480, help="window diameter in pixels (default 480)")
    p.add_argument("--interval", type=float, default=5.0, help="seconds between data fetches (default 5)")
    p.add_argument("--fullscreen", action="store_true", help="fill the screen (for a round Pi display)")
    p.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270],
                   help="rotate the output for a mounted display")
    p.add_argument("--pos", default=os.environ.get("RADAR_POS", ""),
                   help="window position on the desktop, e.g. 20,20")
    p.add_argument("--demo", action="store_true", help="simulated aircraft, no network needed")
    return p.parse_args()


def load_font(size, bold=False):
    import pygame
    for name in ("dejavusansmono", "liberationmono", "couriernew", "monospace"):
        path = pygame.font.match_font(name, bold=bold)
        if path:
            return pygame.font.Font(path, size)
    return pygame.font.Font(None, int(size * 1.3))


class Radar:
    def __init__(self, args):
        if args.pos:
            os.environ["SDL_VIDEO_WINDOW_POS"] = args.pos
        import pygame
        self.pg = pygame
        pygame.display.init()
        pygame.font.init()
        pygame.display.set_caption("Flight Radar")
        pygame.mouse.set_visible(not args.fullscreen)

        self.args = args
        self.fullscreen = args.fullscreen
        self.display = self._open_display()
        self.size = min(self.display.get_size()) if self.fullscreen else args.size
        self.canvas = pygame.Surface((self.size, self.size))
        self.cx = self.cy = self.size // 2
        self.radius = self.size // 2 - 2

        s = self.size / 480
        self.font = load_font(max(9, int(12 * s)))
        self.font_small = load_font(max(8, int(10 * s)))
        self.font_big = load_font(max(10, int(14 * s)), bold=True)
        self.show_labels = True
        self.range_nm = args.range

        self._build_static_layers()
        feed_cls = DemoFeed if args.demo else Feed
        self.feed = feed_cls(args.lat, args.lon, self.range_nm, args.interval).start()
        self.clock = pygame.time.Clock()

    # --- setup -------------------------------------------------------------

    def _open_display(self):
        pg = self.pg
        if self.fullscreen:
            return pg.display.set_mode((0, 0), pg.FULLSCREEN)
        return pg.display.set_mode((self.args.size, self.args.size), pg.NOFRAME)

    def _build_static_layers(self):
        """Pre-render the parts that only change when the range changes."""
        pg, size, cx, cy, r = self.pg, self.size, self.cx, self.cy, self.radius

        # Grid: face, range rings, crosshair, compass ticks
        self.grid = pg.Surface((size, size))
        self.grid.fill(BG)
        pg.draw.circle(self.grid, FACE, (cx, cy), r)
        for i in (1, 2, 3, 4):
            pg.draw.circle(self.grid, GRID if i == 4 else GRID_DIM, (cx, cy), int(r * i / 4), 1)
            if i == 2:  # label the half-range ring; full range is shown in the status line
                label = self.font_small.render(f"{self.range_nm / 2:g}nm", True, GRID)
                self.grid.blit(label, (cx + 3, cy - int(r / 2) + 1))
        pg.draw.line(self.grid, GRID_DIM, (cx - r, cy), (cx + r, cy))
        pg.draw.line(self.grid, GRID_DIM, (cx, cy - r), (cx, cy + r))
        for deg in range(0, 360, 10):
            a = math.radians(deg)
            inner = r - (10 if deg % 30 == 0 else 5)
            pg.draw.line(self.grid, GRID,
                         (cx + inner * math.sin(a), cy - inner * math.cos(a)),
                         (cx + r * math.sin(a), cy - r * math.cos(a)))
        for deg, name in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
            a = math.radians(deg)
            t = self.font_big.render(name, True, TEXT)
            d = r - 22 * size / 480
            self.grid.blit(t, t.get_rect(center=(cx + d * math.sin(a), cy - d * math.cos(a))))

        # Sweep: a fading wedge, pre-rendered pointing north and rotated each frame
        self.sweep = pg.Surface((size, size), pg.SRCALPHA)
        steps = 40
        for i in range(steps):
            a0 = math.radians(-i * 1.5)
            a1 = math.radians(-(i + 1) * 1.5)
            alpha = int(90 * (1 - i / steps) ** 2)
            pts = [(cx, cy),
                   (cx + r * math.sin(a0), cy - r * math.cos(a0)),
                   (cx + r * math.sin(a1), cy - r * math.cos(a1))]
            pg.draw.polygon(self.sweep, (*SWEEP, alpha), pts)
        pg.draw.line(self.sweep, (*SWEEP, 220), (cx, cy), (cx, cy - r), 2)

        # Mask to keep everything inside the circle
        self.mask = pg.Surface((size, size), pg.SRCALPHA)
        self.mask.fill((0, 0, 0, 255))
        pg.draw.circle(self.mask, (0, 0, 0, 0), (cx, cy), r)

    # --- geometry ----------------------------------------------------------

    def project(self, lat, lon):
        """Lat/lon -> (x, y) in nautical miles east/north of home (fine at these ranges)."""
        dx = (lon - self.args.lon) * 60 * math.cos(math.radians(self.args.lat))
        dy = (lat - self.args.lat) * 60
        return dx, dy

    def to_screen(self, dx, dy):
        k = self.radius / self.range_nm
        return self.cx + dx * k, self.cy - dy * k

    # --- drawing -----------------------------------------------------------

    def draw_plane(self, surf, ac, age_s, sweep_deg):
        pg = self.pg
        dx, dy = self.project(ac.lat, ac.lon)
        # Dead-reckon between 5 s updates so movement stays smooth
        if ac.speed_kt and ac.track_deg is not None and ac.alt_ft != 0:
            dist = ac.speed_kt * min(age_s, 30) / 3600
            dx += dist * math.sin(math.radians(ac.track_deg))
            dy += dist * math.cos(math.radians(ac.track_deg))
        if math.hypot(dx, dy) > self.range_nm:
            return
        x, y = self.to_screen(dx, dy)

        # Glow brighter right after the sweep passes over the blip
        bearing = math.degrees(math.atan2(dx, dy)) % 360
        since = (sweep_deg - bearing) % 360 / 360  # 0 = just swept
        glow = 1 - 0.55 * since
        base = GROUND if ac.alt_ft == 0 else PLANE
        col = tuple(int(c * glow) for c in base)

        s = self.size / 480
        if ac.track_deg is not None:
            a = math.radians(ac.track_deg)
            tip = (x + 9 * s * math.sin(a), y - 9 * s * math.cos(a))
            left = (x + 6 * s * math.sin(a + 2.5), y - 6 * s * math.cos(a + 2.5))
            right = (x + 6 * s * math.sin(a - 2.5), y - 6 * s * math.cos(a - 2.5))
            pg.draw.polygon(surf, col, (tip, left, (x, y), right))
            # Short trail line showing ~1 minute of travel ahead
            if ac.speed_kt and ac.alt_ft != 0:
                lead = ac.speed_kt / 60 * self.radius / self.range_nm
                pg.draw.line(surf, tuple(c // 3 for c in col), (x, y),
                             (x + lead * math.sin(a), y - lead * math.cos(a)))
        else:
            pg.draw.circle(surf, col, (int(x), int(y)), int(3 * s))

        if self.show_labels:
            lx, ly = x + 8 * s, y - 2 * s
            name = self.font.render(ac.callsign or ac.hex.upper(), True, col)
            if ac.alt_ft is None:
                alt = "---"
            elif ac.alt_ft == 0:
                alt = "GND"
            else:
                alt = f"FL{round(ac.alt_ft / 100):03d}" if ac.alt_ft >= 10000 else f"{ac.alt_ft}ft"
            info = self.font_small.render(alt, True, tuple(int(c * 0.7) for c in col))
            if lx + name.get_width() > self.cx + self.radius * 0.9:  # flip label left near the edge
                lx = x - 8 * s - name.get_width()
            surf.blit(name, (lx, ly))
            surf.blit(info, (lx, ly + name.get_height() - 2))

    def draw(self):
        pg, surf = self.pg, self.canvas
        now = time.monotonic()
        sweep_deg = (now % SWEEP_PERIOD) / SWEEP_PERIOD * 360

        surf.blit(self.grid, (0, 0))
        rotated = pg.transform.rotate(self.sweep, -sweep_deg)
        surf.blit(rotated, rotated.get_rect(center=(self.cx, self.cy)))

        planes, updated_at, source, error = self.feed.snapshot()
        age = now - updated_at if updated_at else 0
        visible = 0
        for ac in planes:
            dx, dy = self.project(ac.lat, ac.lon)
            if math.hypot(dx, dy) <= self.range_nm:
                visible += 1
            self.draw_plane(surf, ac, age, sweep_deg)

        pg.draw.circle(surf, HOME, (self.cx, self.cy), max(2, int(3 * self.size / 480)))
        surf.blit(self.mask, (0, 0))
        pg.draw.circle(surf, GRID, (self.cx, self.cy), self.radius, 2)

        # Status readout along the bottom of the dial
        if not updated_at:
            status, col = ("NO DATA  " + error) if error else "SCANNING...", WARN if error else TEXT_DIM
        elif error or age > self.args.interval * 3:
            status, col = f"STALE {int(age)}s", WARN
        else:
            status, col = f"{visible} AC  {self.range_nm:g}NM  {source}", TEXT_DIM
        t = self.font_small.render(status[:40], True, col)
        surf.blit(t, t.get_rect(center=(self.cx, self.cy + self.radius * 0.62)))

        out = surf if not self.args.rotate else pg.transform.rotate(surf, self.args.rotate)
        self.display.fill(BG)
        self.display.blit(out, out.get_rect(center=self.display.get_rect().center))
        pg.display.flip()

    # --- main loop ---------------------------------------------------------

    def set_range(self, step):
        idx = min(range(len(RANGES_NM)), key=lambda i: abs(RANGES_NM[i] - self.range_nm))
        self.range_nm = RANGES_NM[max(0, min(len(RANGES_NM) - 1, idx + step))]
        self.feed.radius_nm = self.range_nm
        self._build_static_layers()

    def run(self):
        pg = self.pg
        try:
            while True:
                for ev in pg.event.get():
                    if ev.type == pg.QUIT:
                        return
                    if ev.type == pg.KEYDOWN:
                        if ev.key in (pg.K_ESCAPE, pg.K_q):
                            return
                        if ev.key in (pg.K_PLUS, pg.K_EQUALS, pg.K_KP_PLUS, pg.K_UP):
                            self.set_range(-1)  # zoom in
                        elif ev.key in (pg.K_MINUS, pg.K_KP_MINUS, pg.K_DOWN):
                            self.set_range(+1)  # zoom out
                        elif ev.key == pg.K_l:
                            self.show_labels = not self.show_labels
                    if ev.type == pg.MOUSEWHEEL:
                        self.set_range(-1 if ev.y > 0 else 1)
                    # Tap/click cycles through ranges (handy on a touch-screen round display)
                    if ev.type == pg.MOUSEBUTTONUP and ev.button == 1:
                        if self.range_nm >= RANGES_NM[-1]:
                            self.set_range(-len(RANGES_NM))
                        else:
                            self.set_range(+1)
                self.draw()
                self.clock.tick(30)
        finally:
            self.feed.stop()
            pg.quit()


def main():
    Radar(parse_args()).run()


if __name__ == "__main__":
    main()
