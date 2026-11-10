"""Read GPX/TCX tracks, find the runs that start from the same place, and clean their timing."""
import glob
import gzip
import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

import numpy as np


@dataclass
class Track:
    name: str
    start: datetime
    sport: str
    loc: np.ndarray   # (n, 2) lat, lon
    t: np.ndarray     # (n,) seconds since start


def metres_per_degree(lat):
    return 110540.0, 111320.0 * math.cos(math.radians(lat))


def _tag(el):
    return el.tag.rsplit("}", 1)[-1]


def _time(s):
    return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))


def read_track(path):
    """Points and timestamps from a .gpx or .tcx file (optionally .gz). None if it has no timed points."""
    opener = gzip.open if path.endswith(".gz") else open
    pts, times, sport = [], [], ""
    with opener(path, "rb") as fh:
        for _, el in ET.iterparse(fh):
            tag = _tag(el)
            if tag == "type" and not sport and el.text:          # GPX: <trk><type>running</type>
                sport = el.text.strip().lower()
            elif tag == "Activity" and not sport:                 # TCX: <Activity Sport="Running">
                sport = (el.get("Sport") or "").lower()
            elif tag == "trkpt":
                t = next((c.text for c in el if _tag(c) == "time"), None)
                if t:
                    pts.append((float(el.get("lat")), float(el.get("lon"))))
                    times.append(_time(t))
                el.clear()
            elif tag == "Trackpoint":
                t = next((c.text for c in el if _tag(c) == "Time"), None)
                pos = next((c for c in el if _tag(c) == "Position"), None)
                if t and pos is not None:
                    v = {_tag(c): float(c.text) for c in pos}
                    pts.append((v["LatitudeDegrees"], v["LongitudeDegrees"]))
                    times.append(_time(t))
                el.clear()
    if len(pts) < 10:
        return None
    t0 = times[0]
    t = np.array([(x - t0).total_seconds() for x in times], float)
    return Track(os.path.basename(path), t0, sport, np.array(pts, float), t)


def find_files(inputs):
    exts = (".gpx", ".tcx", ".gpx.gz", ".tcx.gz")
    out = []
    for p in inputs:
        if os.path.isdir(p):
            out += [f for f in glob.glob(os.path.join(p, "**", "*"), recursive=True) if f.lower().endswith(exts)]
        elif p.lower().endswith(exts):
            out.append(p)
    return sorted(set(out))


def clean(tr, max_speed=7.0, max_rejects=10):
    """Drop GPS spikes and cap pauses.

    A point is a spike if reaching it from the last good point needs more than `max_speed` m/s.
    If `max_rejects` points in a row are spikes, the signal is gone (a tunnel, the metro) and the
    track is cut at the last good point instead of drawing a jump across the map.
    Pauses are capped at 3x the median sampling interval so stops at traffic lights don't freeze runners.
    """
    ky, kx = metres_per_degree(tr.loc[0, 0])
    keep, rejects = [0], 0
    for i in range(1, len(tr.t)):
        j = keep[-1]
        dt = tr.t[i] - tr.t[j]
        if dt <= 0:
            continue
        d = math.hypot((tr.loc[i, 0] - tr.loc[j, 0]) * ky, (tr.loc[i, 1] - tr.loc[j, 1]) * kx)
        if d > 50 and d / dt > max_speed:
            rejects += 1
            if rejects >= max_rejects:
                break
            continue
        rejects = 0
        keep.append(i)
    loc, t = tr.loc[keep], tr.t[keep]
    dt = np.diff(t)
    t = np.concatenate([[0], np.cumsum(np.minimum(dt, 3 * np.median(dt)))])
    return Track(tr.name, tr.start, tr.sport, loc, t)


def find_home(starts, radius):
    """The start point with the most other starts within `radius` metres (mean of that cluster)."""
    ky, kx = metres_per_degree(float(np.median(starts[:, 0])))
    dy = (starts[:, None, 0] - starts[None, :, 0]) * ky
    dx = (starts[:, None, 1] - starts[None, :, 1]) * kx
    near = np.hypot(dy, dx) <= radius
    i = int(np.argmax(near.sum(1)))
    return starts[near[i]].mean(0)


def dist_to(loc, p):
    ky, kx = metres_per_degree(p[0])
    return np.hypot((loc[:, 0] - p[0]) * ky, (loc[:, 1] - p[1]) * kx)


def hide_ends(tr, home, radius):
    """Remove the start and end of a track inside `radius` metres of home (like a privacy zone)."""
    out = dist_to(tr.loc, home) > radius
    if out.sum() < 10:
        return None
    a = int(np.argmax(out))
    b = len(out) - int(np.argmax(out[::-1]))
    t = tr.t[a:b] - tr.t[a]
    return Track(tr.name, tr.start, tr.sport, tr.loc[a:b], t)
