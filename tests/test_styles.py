import json
import shutil

import numpy as np
import pytest
from PIL import Image

from homeruns import overlay, styles
from homeruns.__main__ import overlay_only, parse_args


def test_every_style_has_scene_and_text_colours():
    assert set(styles.SCENE) == set(styles.TEXT) == set(styles.NAMES)
    assert styles.SCENE["dark"] is None   # the video's look is the scene as scene.py builds it


def test_style_option():
    assert parse_args(["runs"]).style is None
    assert parse_args(["runs", "--style", "paper"]).style == "paper"
    with pytest.raises(SystemExit):
        parse_args(["runs", "--style", "sepia"])


def test_overlay_only_keeps_the_style_of_the_kept_frames(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    Image.new("RGB", (8, 8)).save(frames / "f_0001.png")
    (frames / "overlay.json").write_text(json.dumps({"cfg": {"style": "paper"}, "routes": []}))
    with pytest.raises(SystemExit, match="kept frames are in the paper style"):
        overlay_only(parse_args(["--overlay-only", "--style", "mono", "--work", str(tmp_path)]))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
@pytest.mark.parametrize("style", styles.NAMES)
def test_overlay_uses_the_style_colours(tmp_path, style):
    W, H, grey = 216, 384, 128
    for f in (1, 2):
        Image.new("RGB", (W, H), (grey,) * 3).save(tmp_path / f"f_{f:04d}.png")
    cfg = dict(fps=30, intro=1, run_frames=10, time_k=2.3, ramp=1, style=style)
    out = tmp_path / "v.mp4"
    overlay.encode(cfg, [{"duration": 600}, {"duration": 900}], tmp_path, str(out), "TITLE", "sub", log=lambda *a: None)
    im = np.asarray(Image.open(tmp_path / "v_final.png"), np.float32)
    P = styles.TEXT[style]
    # the top edge is fully under the scrim: the map fades towards the scrim colour
    want = grey * (1 - P["scrim_k"]) + np.array(P["scrim"]) * P["scrim_k"]
    assert np.abs(im[1, W - 2] - want).max() <= 2
    # the title is drawn in the style's main colour
    title = im[int(H * 0.11):int(H * 0.16), :W // 2].reshape(-1, 3)
    assert np.abs(title - np.array(P["main"])).sum(1).min() < 30
