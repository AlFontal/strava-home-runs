import io
import json

from PIL import Image

from homeruns import basemap

BOX = 38.70, 38.71, -9.15, -9.14   # S, N, W, E


def fake_fetch(calls):
    def fetch(url, data=None, timeout=180):
        calls.append(url)
        if url == basemap.OVERPASS:
            return b'{"elements": []}'
        buf = io.BytesIO()
        Image.new("RGB", (256, 256), (128, 0, 0)).save(buf, "PNG")   # Terrarium for 0 m
        return buf.getvalue()
    return fetch


def test_fetch_reuses_a_current_cache_and_downloads_an_older_one_again(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(basemap, "_fetch", fake_fetch(calls))
    quiet = lambda *a: None  # noqa: E731

    basemap.fetch(tmp_path, *BOX, log=quiet)
    n = len(calls)
    assert calls[-1] == basemap.OVERPASS

    basemap.fetch(tmp_path, *BOX, log=quiet)
    assert len(calls) == n                                            # cached

    bb = json.loads((tmp_path / "bbox.json").read_text())
    del bb["osm"]                                                     # as written before the version existed
    (tmp_path / "bbox.json").write_text(json.dumps(bb))
    basemap.fetch(tmp_path, *BOX, log=quiet)
    assert len(calls) == 2 * n
    assert json.loads((tmp_path / "bbox.json").read_text())["osm"] == basemap.OSM_VERSION
