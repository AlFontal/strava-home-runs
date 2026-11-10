"""Turn the basemap and the tracks into Blender-ready assets (plain Python, runs before Blender).

<work>/streets.png  RGB texture: R = streets (width by road class), G = parks, B = sea
<work>/routes.json  per run: xyz points in km (terrain-local) every STEP seconds
"""
import json
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

Image.MAX_IMAGE_PIXELS = None

STEP = 5.0          # seconds between resampled route points
EXAG = 3.0          # vertical exaggeration of the terrain
LIFT_M = 6.0        # route height above ground, metres (before exaggeration)
PX_PER_KM = 1380    # street texture resolution (~0.7 m per pixel)
MAX_TEX = 16384     # GPU texture limit
BASE_PX_PER_KM = 686  # road widths below were tuned at this resolution
ROAD_W = {"motorway": 9, "trunk": 9, "primary": 8, "secondary": 7, "tertiary": 6,
          "motorway_link": 5, "trunk_link": 5, "primary_link": 5, "secondary_link": 5, "tertiary_link": 5,
          "residential": 4, "unclassified": 4, "living_street": 3, "pedestrian": 4,
          "cycleway": 2, "footway": 2, "steps": 2, "path": 2, "track": 2}


def prepare(basemap_dir, work_dir, tracks, sea=True, log=print):
    bb = json.load(open(os.path.join(basemap_dir, "bbox.json")))
    S, N, W, E = bb["S"], bb["N"], bb["W"], bb["E"]
    lat0 = (S + N) / 2
    WKM = (E - W) * 111.320 * math.cos(math.radians(lat0))
    HKM = (N - S) * 110.540
    dem = np.load(os.path.join(basemap_dir, "dem.npy"))
    tex_h = int(min(MAX_TEX, max(2048, HKM * PX_PER_KM)))
    tex_w = int(min(MAX_TEX, tex_h * WKM / HKM))
    tex_h = int(tex_w * HKM / WKM) if tex_w == MAX_TEX else tex_h
    px = (tex_h / HKM) / BASE_PX_PER_KM
    log(f"terrain {WKM:.1f} x {HKM:.1f} km, street texture {tex_w}x{tex_h}")

    def to_px(lat, lon):
        return (lon - W) / (E - W) * tex_w, (N - lat) / (N - S) * tex_h

    def to_km(lat, lon):
        return (lon - W) / (E - W) * WKM - WKM / 2, (lat - S) / (N - S) * HKM - HKM / 2

    def dem_at(lat, lon):
        r = (N - lat) / (N - S) * (dem.shape[0] - 1)
        c = (lon - W) / (E - W) * (dem.shape[1] - 1)
        r0 = int(np.clip(math.floor(r), 0, dem.shape[0] - 2))
        c0 = int(np.clip(math.floor(c), 0, dem.shape[1] - 2))
        fr, fc = r - r0, c - c0
        q = dem[r0:r0 + 2, c0:c0 + 2]
        return q[0, 0] * (1 - fr) * (1 - fc) + q[0, 1] * (1 - fr) * fc + q[1, 0] * fr * (1 - fc) + q[1, 1] * fr * fc

    # ---- street texture ----
    osm = json.load(open(os.path.join(basemap_dir, "streets.json")))["elements"]
    streets = Image.new("L", (tex_w, tex_h), 0)
    parks = Image.new("L", (tex_w, tex_h), 0)
    ds, dp = ImageDraw.Draw(streets), ImageDraw.Draw(parks)
    for el in osm:
        g = el.get("geometry")
        if not g:
            continue
        pts = [to_px(p["lat"], p["lon"]) for p in g]
        t = el.get("tags", {})
        if t.get("leisure") == "park":
            if len(pts) > 2:
                dp.polygon(pts, fill=255)
        elif t.get("railway") == "rail":
            ds.line(pts, fill=110, width=max(1, round(3 * px)))
        else:
            w = ROAD_W.get(t.get("highway"), 2)
            val = 255 if w >= 6 else (190 if w >= 3 else 120)
            ds.line(pts, fill=val, width=max(1, round(w * px)), joint="curve")
    parks = parks.filter(ImageFilter.GaussianBlur(3 * px))
    sea_mask = (dem <= 0.5) if sea else np.zeros_like(dem, bool)
    water = Image.fromarray((sea_mask * 255).astype(np.uint8)).resize((tex_w, tex_h), Image.BILINEAR)
    water = water.filter(ImageFilter.GaussianBlur(4 * px))
    Image.merge("RGB", (streets, parks, water)).save(os.path.join(work_dir, "streets.png"))

    # ---- routes ----
    routes = []
    for i, tr in enumerate(tracks):
        tt = np.arange(0, tr.t[-1] + STEP, STEP)
        tt[-1] = min(tt[-1], tr.t[-1])
        la, lo = np.interp(tt, tr.t, tr.loc[:, 0]), np.interp(tt, tr.t, tr.loc[:, 1])
        pts = []
        for a, b in zip(la, lo):
            x, y = to_km(a, b)
            z = (max(dem_at(a, b), 0) + LIFT_M) / 1000 * EXAG
            pts.append([round(x, 5), round(y, 5), round(z, 5)])
        routes.append({"id": i, "name": tr.name, "date": tr.start.isoformat(), "duration": float(tr.t[-1]),
                       "step": STEP, "pts": pts})
    json.dump({"WKM": WKM, "HKM": HKM, "EXAG": EXAG, "routes": routes}, open(os.path.join(work_dir, "routes.json"), "w"))
    return WKM, HKM
