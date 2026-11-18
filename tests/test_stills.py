import numpy as np
import pytest
from PIL import Image

from homeruns import overlay
from homeruns.__main__ import parse_args
from homeruns.styles import NAMES


def test_stills_without_a_value_means_all_styles():
    assert parse_args(["runs", "--stills"]).stills == list(NAMES)
    assert parse_args(["runs", "--stills", "--no-video"]).stills == list(NAMES)
    assert parse_args(["runs"]).stills is None


def test_stills_takes_a_comma_separated_list():
    assert parse_args(["runs", "--stills", "paper, mono,paper"]).stills == ["paper", "mono"]
    assert parse_args(["--stills=mono", "runs"]).inputs == ["runs"]


@pytest.mark.parametrize("argv, message", [
    (["--stills", "runs"], "put it after the files"),       # the folder was taken as the style
    (["runs", "--stills", "sepia"], "unknown still style 'sepia'"),
    (["runs", "--stills", ""], "--stills got no style"),
    (["runs", "--stills", ","], "--stills got no style"),
    (["runs", "--no-video"], "add --stills"),
    (["--overlay-only", "--stills"], "not --overlay-only"),
])
def test_bad_stills_options(argv, message, capsys):
    with pytest.raises(SystemExit):
        parse_args(argv)
    assert message in capsys.readouterr().err


@pytest.mark.parametrize("size", [(1440, 2560), (2880, 5120), (1080, 1080)])   # --preview, default, a square --size
@pytest.mark.parametrize("bg", [(240, 238, 232), (4, 6, 12)])
def test_still_is_cropped_to_the_map_with_a_contrasting_credit_below_it(tmp_path, size, bg):
    src, out = tmp_path / "raw.png", tmp_path / "still.png"
    Image.new("RGB", size, bg).save(src)
    overlay.still(src, out)
    im = np.asarray(Image.open(out), np.float32).mean(2)
    s = size[1] / 1920   # the camera keeps the map in y = 461..1363 at 1920 high; 60 px of air around it
    assert im.shape == (round((1363 + 60) * s) - round((461 - 60) * s), size[0])
    ink = np.abs(im - np.mean(bg)) > 20
    rows = np.nonzero(ink.any(1))[0]
    assert rows.size and rows.min() >= im.shape[0] - 60 * s   # the credit is there, in the air below the map
    # dark on a light map, light on a dark one
    assert (im[ink] < np.mean(bg)).all() if np.mean(bg) > 128 else (im[ink] > np.mean(bg)).all()
