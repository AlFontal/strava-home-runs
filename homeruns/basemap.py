"""Download terrain (AWS Terrarium elevation tiles) and OpenStreetMap streets for a bounding box.

Writes into <out_dir>/: dem.npy (metres, north-up), streets.json (raw Overpass response), bbox.json.
Skips the download when <out_dir> already holds the same bbox.
"""
import io
import json
import math
import os
import urllib.parse
import urllib.request

import numpy as np
from PIL import Image

ZOOM = 14
UA = {"User-Agent": "strava-home-runs/0.1"}
OVERPASS = "https://overpass-api.de/api/interpreter"
HIGHWAYS = ("motorway|trunk|primary|secondary|tertiary|motorway_link|trunk_link|primary_link|secondary_link|"
            "tertiary_link|residential|unclassified|living_street|pedestrian|track|path|footway|steps|cycleway")


def _tile_xy(lat, lon, z):
    n = 2 ** z
    return (lon + 180) / 360 * n, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n


def _fetch(url, data=None, timeout=180):
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch(out_dir, S, N, W, E, log=print):
    os.makedirs(out_dir, exist_ok=True)
    bb_path = os.path.join(out_dir, "bbox.json")
    want = {"S": round(S, 5), "N": round(N, 5), "W": round(W, 5), "E": round(E, 5)}
    if os.path.exists(bb_path):
        have = json.load(open(bb_path))
        if all(abs(have[k] - want[k]) < 1e-5 for k in want) and os.path.exists(os.path.join(out_dir, "streets.json")):
            log("basemap: cached")
            return

    x0f, y0f = _tile_xy(N, W, ZOOM)
    x1f, y1f = _tile_xy(S, E, ZOOM)
    tx0, ty0, tx1, ty1 = int(x0f), int(y0f), int(x1f), int(y1f)
    n_tiles = (tx1 - tx0 + 1) * (ty1 - ty0 + 1)
    log(f"basemap: {n_tiles} elevation tiles")
    mosaic = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), np.float32)
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            png = _fetch(f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{ZOOM}/{tx}/{ty}.png")
            a = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), np.float32)
            mosaic[(ty - ty0) * 256:(ty - ty0 + 1) * 256, (tx - tx0) * 256:(tx - tx0 + 1) * 256] = \
                a[..., 0] * 256 + a[..., 1] + a[..., 2] / 256 - 32768
    px0, py0 = int((x0f - tx0) * 256), int((y0f - ty0) * 256)
    px1, py1 = int((x1f - tx0) * 256), int((y1f - ty0) * 256)
    dem = mosaic[py0:py1, px0:px1]   # Mercator rows vs latitude: < 0.1% error over ~20 km
    np.save(os.path.join(out_dir, "dem.npy"), dem)

    log("basemap: OpenStreetMap streets (Overpass, can take a minute)")
    q = f"""[out:json][timeout:170];
(
  way["highway"~"^({HIGHWAYS})$"]({S},{W},{N},{E});
  way["railway"="rail"]({S},{W},{N},{E});
  way["leisure"="park"]({S},{W},{N},{E});
);
out geom;"""
    raw = _fetch(OVERPASS, urllib.parse.urlencode({"data": q}).encode())
    open(os.path.join(out_dir, "streets.json"), "wb").write(raw)
    json.dump({**want, "shape": list(dem.shape)}, open(bb_path, "w"))
    log(f"basemap: {len(json.loads(raw)['elements'])} map features")
