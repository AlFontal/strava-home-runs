import gzip
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from homeruns import tracks

T0 = datetime(2024, 5, 1, 7, 0, tzinfo=timezone.utc)
LAT, LON = 38.7, -9.14
STEP = 3 / 110540          # degrees of latitude per second at 3 m/s


def points(n=20):
    return [(LAT + i * STEP, LON, T0 + timedelta(seconds=i)) for i in range(n)]


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def gpx(pts):
    trkpts = "".join(f'<trkpt lat="{la}" lon="{lo}"><time>{iso(t)}</time></trkpt>' for la, lo, t in pts)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
            f'<trk><type>running</type><trkseg>{trkpts}</trkseg></trk></gpx>')


def tcx(pts, extra=""):
    tps = "".join(f"<Trackpoint><Time>{iso(t)}</Time><Position><LatitudeDegrees>{la}</LatitudeDegrees>"
                  f"<LongitudeDegrees>{lo}</LongitudeDegrees></Position></Trackpoint>" for la, lo, t in pts)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">'
            f'<Activities><Activity Sport="Running"><Lap><Track>{tps}{extra}</Track></Lap></Activity></Activities>'
            '</TrainingCenterDatabase>')


def write(path, text, prefix=b""):
    data = prefix + text.encode()
    path.write_bytes(gzip.compress(data) if path.name.endswith(".gz") else data)
    return str(path)


def track(lat, t=None):
    loc = np.column_stack([lat, np.full(len(lat), LON)])
    return tracks.Track("t", T0, "running", loc, np.arange(len(lat), dtype=float) if t is None else t)


# ---- reading files ----

@pytest.mark.parametrize("name, make", [("a.gpx", gpx), ("a.tcx", tcx), ("a.gpx.gz", gpx), ("a.tcx.gz", tcx)])
def test_read_track(tmp_path, name, make):
    tr = tracks.read_track(write(tmp_path / name, make(points())))
    assert tr.sport == "running"
    assert tr.start == T0
    assert len(tr.t) == 20 and tr.t[-1] == 19
    np.testing.assert_allclose(tr.loc[0], [LAT, LON])


@pytest.mark.parametrize("prefix", [b"          ", b"\xef\xbb\xbf", b"\xef\xbb\xbf  \r\n"])
def test_read_track_ignores_whitespace_and_bom_before_xml_declaration(tmp_path, prefix):
    # Strava's bulk export writes some TCX files with spaces before <?xml ...?>
    tr = tracks.read_track(write(tmp_path / "a.tcx.gz", tcx(points()), prefix))
    assert tr is not None and len(tr.t) == 20


def test_read_track_skips_tcx_trackpoints_without_position(tmp_path):
    hr_only = "<Trackpoint><Time>2024-05-01T07:01:00Z</Time><HeartRateBpm><Value>150</Value></HeartRateBpm></Trackpoint>"
    tr = tracks.read_track(write(tmp_path / "a.tcx", tcx(points(), extra=hr_only)))
    assert len(tr.t) == 20


def test_read_track_returns_none_for_truncated_xml(tmp_path):
    assert tracks.read_track(write(tmp_path / "a.tcx", tcx(points())[:400])) is None


def test_read_track_returns_none_for_short_tracks(tmp_path):
    assert tracks.read_track(write(tmp_path / "a.gpx", gpx(points(5)))) is None


def test_find_files(tmp_path):
    (tmp_path / "sub").mkdir()
    for name in ["a.gpx", "b.TCX", "sub/c.fit.gz", "d.csv", "e.gz", "sub/f.png"]:
        (tmp_path / name).write_bytes(b"")
    found = [os.path.relpath(f, tmp_path) for f in tracks.find_files([str(tmp_path)])]
    assert found == ["a.gpx", "b.TCX", os.path.join("sub", "c.fit.gz")]


# ---- cleaning ----

def test_clean_drops_a_gps_spike():
    lat = LAT + np.arange(30) * STEP
    lat[10] += 0.01                     # one sample ~1 km off
    out = tracks.clean(track(lat))
    assert len(out.t) == 29
    assert out.loc[:, 0].max() < LAT + 30 * STEP


def test_clean_cuts_the_track_when_the_signal_is_lost():
    lat = LAT + np.arange(30) * STEP
    lat[10:] += 0.05                    # jumps ~5 km away and stays there (tunnel, metro)
    out = tracks.clean(track(lat))
    assert len(out.t) == 10


def test_clean_caps_pauses():
    t = np.arange(30, dtype=float)
    t[15:] += 120                       # two minutes stopped at a traffic light
    out = tracks.clean(track(LAT + np.arange(30) * STEP, t))
    assert out.t[-1] == 29 + 2          # the 121 s gap becomes 3x the 1 s median


# ---- home ----

def test_find_home_ignores_outlying_starts():
    home = np.array([LAT, LON])
    rng = np.random.default_rng(0)
    starts = np.vstack([home + rng.normal(0, 2e-5, (8, 2)),       # within a few metres of home
                        [[LAT + 0.05, LON], [LAT, LON + 0.05], [LAT - 0.03, LON - 0.03]]])
    found = tracks.find_home(starts, radius=50)
    assert tracks.dist_to(found[None], home)[0] < 10


def test_hide_ends_trims_both_ends_near_home():
    d = np.r_[0:60, 60:0:-1]            # out and back, 3 m per sample
    tr = track(LAT + d * STEP)
    home = np.array([LAT, LON])
    out = tracks.hide_ends(tr, home, radius=100)
    assert tracks.dist_to(out.loc, home).min() > 100
    assert len(out.t) == 53             # samples 34..86, the ones more than 100 m away
    assert out.t[0] == 0


def test_hide_ends_returns_none_when_the_run_stays_near_home():
    tr = track(LAT + np.arange(30) * STEP)
    assert tracks.hide_ends(tr, np.array([LAT, LON]), radius=1000) is None
