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


def irregular():
    # a fix every 1-3 s, and now and then none for 5-15 s (a phone saving battery, tall buildings)
    rng = np.random.default_rng(0)
    dt = rng.integers(1, 4, 250)
    dt[rng.choice(250, 20, replace=False)] = rng.integers(5, 16, 20)
    return dt


# seconds between samples, as different devices and apps record them
SAMPLING = {
    "fixed-1s": np.ones(600),
    "fixed-5s": np.full(120, 5),
    "irregular": irregular(),
    "mixed-1s-8s": np.tile(np.r_[np.ones(40), np.full(10, 8)], 4),     # e.g. Garmin's smart recording
}


@pytest.fixture(params=list(SAMPLING))
def sample_times(request):
    """Sample times (s) of a run without stops, recorded with each sampling pattern."""
    return np.r_[0, np.cumsum(SAMPLING[request.param])].astype(float)


def test_clean_keeps_the_timing_of_a_run_without_stops(sample_times):
    t = sample_times
    out = tracks.clean(track(LAT + t * STEP, t))
    np.testing.assert_array_equal(out.t, t)         # every gap was covered at 3 m/s, none is a pause


def test_clean_caps_a_stop_at_3x_the_median_interval(sample_times):
    t, k = sample_times, len(sample_times) // 2
    i = np.r_[0:k + 1, k:len(t)]                    # at sample k's position twice: standing at a light ...
    ts = np.r_[t[:k + 1], t[k:] + 120]              # ... for two minutes, nothing recorded in between
    out = tracks.clean(track(LAT + t[i] * STEP, ts))
    gaps = np.diff(ts)
    gaps[k] = 3 * np.median(gaps)                   # only the stop is shortened
    np.testing.assert_array_equal(np.diff(out.t), gaps)


def test_clean_shortens_a_slow_gap_to_the_time_at_pause_speed():
    t = np.arange(60.0)
    t[30:] += 119                                   # two minutes between samples 29 and 30 ...
    m = 3 * np.arange(60.0)
    m[30:] += 57                                    # ... in which the runner walked 60 m
    out = tracks.clean(track(LAT + m * STEP / 3, t))
    assert np.diff(out.t)[29] == pytest.approx(60)  # 60 m at the 1 m/s pause_speed, not 3x the 1 s median


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
