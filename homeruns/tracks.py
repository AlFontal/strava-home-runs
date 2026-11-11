"""Read GPX/TCX/FIT tracks, find the runs that start from the same place, and clean their timing."""
import glob
import gzip
import math
import os
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone

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


FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc).timestamp()
# FIT sport enum (profile "sport" type); unknown numbers are kept as "sport_<n>"
FIT_SPORTS = {0: "generic", 1: "running", 2: "cycling", 3: "transition", 4: "fitness_equipment", 5: "swimming",
              10: "training", 11: "walking", 13: "alpine_skiing", 15: "rowing", 16: "mountaineering",
              17: "hiking", 19: "paddling", 21: "e_biking", 37: "stand_up_paddleboarding"}


def _read_fit(data):
    """(sport, [(unix seconds, lat, lon), ...]) from the record messages of a FIT file.

    A minimal reader for the parts of the format that activity files use: definition and data
    messages, compressed timestamp headers, developer fields (skipped) and chained files.
    """
    sport, out, start = "", [], 0
    while start + 12 <= len(data) and data[start + 8:start + 12] == b".FIT":
        hsize = data[start]
        end = start + hsize + struct.unpack_from("<I", data, start + 4)[0]
        pos, defs, last_ts = start + hsize, {}, None
        while pos < end:
            h = data[pos]
            pos += 1
            ts = None
            if h & 0x80:                          # compressed timestamp header: 5-bit offset from the last timestamp
                local, off = (h >> 5) & 0x3, h & 0x1F
                if last_ts is not None:
                    ts = last_ts - (last_ts & 0x1F) + off + (0x20 if off < (last_ts & 0x1F) else 0)
                    last_ts = ts
            elif h & 0x40:                        # definition message
                local = h & 0x0F
                endian = "<" if data[pos + 1] == 0 else ">"
                gnum = struct.unpack_from(endian + "H", data, pos + 2)[0]
                n = data[pos + 4]
                pos += 5
                fields = [tuple(data[pos + 3 * i:pos + 3 * i + 2]) for i in range(n)]
                pos += 3 * n
                dev = 0
                if h & 0x20:                      # developer fields: only their sizes matter
                    nd = data[pos]
                    dev = sum(data[pos + 2 + 3 * i] for i in range(nd))
                    pos += 1 + 3 * nd
                defs[local] = (endian, gnum, fields, dev)
                continue
            else:
                local = h & 0x0F
            endian, gnum, fields, dev = defs[local]
            vals = {}
            for num, size in fields:
                if size == 4 and num in (0, 1, 253):
                    vals[num] = struct.unpack_from(endian + ("I" if num == 253 else "i"), data, pos)[0]
                elif size == 1 and num in (0, 5):
                    vals[num] = data[pos]
                pos += size
            pos += dev
            if vals.get(253, 0xFFFFFFFF) != 0xFFFFFFFF:
                ts = last_ts = vals[253]
            if gnum == 20 and ts is not None:     # record
                lat, lon = vals.get(0), vals.get(1)
                if lat not in (None, 0x7FFFFFFF) and lon not in (None, 0x7FFFFFFF):
                    out.append((ts + FIT_EPOCH, lat * 180 / 2 ** 31, lon * 180 / 2 ** 31))
            elif not sport and gnum in (12, 18):  # sport.sport (field 0) or session.sport (field 5)
                v = vals.get(5 if gnum == 18 else 0, 0xFF)
                if v != 0xFF:
                    sport = FIT_SPORTS.get(v, f"sport_{v}")
        start = end + 2                           # skip the CRC; another FIT file may follow
    return sport, out


def read_track(path):
    """Points and timestamps from a .gpx, .tcx or .fit file (optionally .gz). None if it has no timed points."""
    opener = gzip.open if path.lower().endswith(".gz") else open
    if path.lower().removesuffix(".gz").endswith(".fit"):
        with opener(path, "rb") as fh:
            try:
                sport, recs = _read_fit(fh.read())
            except (KeyError, IndexError, struct.error):   # truncated or not really FIT
                return None
        if len(recs) < 10:
            return None
        recs.sort(key=lambda r: r[0])
        t0 = datetime.fromtimestamp(recs[0][0], timezone.utc)
        t = np.array([r[0] - recs[0][0] for r in recs], float)
        return Track(os.path.basename(path), t0, sport, np.array([r[1:] for r in recs], float), t)
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
    exts = (".gpx", ".tcx", ".fit", ".gpx.gz", ".tcx.gz", ".fit.gz")
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
