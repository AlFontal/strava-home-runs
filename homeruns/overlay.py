"""Overlay the title, clock and finish-time histogram on the rendered frames and encode an MP4.

Layout is designed for 9:16 and scales with the frame width. Frame numbers come from the file names,
so preview renders (every 2nd frame) keep real timing at half the frame rate.
"""
import glob
import math
import os
import re
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import timewarp

# (path, face index) candidates; the first one that exists wins
FONTS = {
    "title": [("/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf", 0),
              ("/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf", 0),
              ("C:/Windows/Fonts/arialnb.ttf", 0), ("C:/Windows/Fonts/arialbd.ttf", 0)],
    "text": [("/System/Library/Fonts/Avenir Next.ttc", 5),
             ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
             ("C:/Windows/Fonts/segoeui.ttf", 0), ("C:/Windows/Fonts/arial.ttf", 0)],
    "clock": [("/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf", 0),
              ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 0),
              ("C:/Windows/Fonts/arialbd.ttf", 0)],
}

WHITE, GREY, HOT, DIM, AXIS = (232, 232, 238), (138, 143, 163), (255, 110, 30), (70, 40, 34), (90, 94, 110)
# positions in px at 1080 x 1920; the map is framed between y = 461 and y = 1363 by the camera.
# sub_gap: from the title baseline to the top of the subtitle line
LAYOUT = dict(title_size=120, title_y=232, sub_size=34, sub_gap=40, clock_size=100, clock_y=1392,
              status_y=1502, credit_y=1640, credit_size=20, margin=72, hist=dict(x0=72, x1=600, top=1392, base=1512, label_size=22))


def load_font(kind, size, override=None):
    cands = [(override, 0)] if override else FONTS[kind]
    for path, idx in cands:
        if path and os.path.exists(path):
            return ImageFont.truetype(path, size, index=idx)
    return ImageFont.load_default(size)


def _hist_scale(tmax):
    """Bin width (minutes) and axis ticks [(minutes, label)] that suit the longest run."""
    mins = tmax / 60
    if mins <= 75:
        return 2.5, [(m, f"{m}m") for m in range(0, int(mins) + 1, 15)]
    if mins <= 150:
        return 5, [(m, f"{m // 60}h{m % 60:02d}" if m % 60 else f"{m // 60}h") for m in range(0, int(mins) + 1, 30)]
    return 5, [(m, f"{m // 60}h") for m in range(0, int(mins) + 1, 60)]


def encode(cfg, routes, frames_dir, out, title, subtitle, fonts=None, crf=16, log=print):
    fonts = fonts or {}
    FPS, INTRO, RUN_FRAMES = cfg["fps"], cfg["intro"], cfg["run_frames"]
    durs = np.array([r["duration"] for r in routes])
    tmax = float(durs.max())

    def run_time(f):
        return timewarp.run_time(f, INTRO, RUN_FRAMES, cfg["time_k"], tmax, cfg["ramp"])

    frames = sorted(glob.glob(os.path.join(frames_dir, "f_*.png")))
    nums = [int(re.search(r"(\d+)\.png$", p).group(1)) for p in frames]
    step = nums[1] - nums[0] if len(nums) > 1 else 1
    W, H = Image.open(frames[0]).size
    k = W / 1080
    px = lambda v: round(v * k)  # noqa: E731
    L = LAYOUT
    f_title = load_font("title", px(L["title_size"]), fonts.get("title"))
    f_sub = load_font("text", px(L["sub_size"]), fonts.get("text"))
    f_clock = load_font("clock", px(L["clock_size"]), fonts.get("clock"))
    M = px(L["margin"])
    # shrink the title until it fits on one line
    while f_title.getlength(title) > W - 2 * M and f_title.size > 20:
        f_title = load_font("title", f_title.size - 4, fonts.get("title"))
    # the subtitle follows the title's baseline, so it stays close when a long title is shrunk
    sub_y = px(L["title_y"]) + f_title.getbbox("H")[3] + px(L["sub_gap"])

    # dark gradients behind the text so routes passing underneath don't hurt legibility
    y = np.arange(H, dtype=np.float32)[:, None]
    top_end, bot_start = px(476), px(L["clock_y"]) - px(90)   # scrim ends where it always has, below the subtitle
    top = np.clip((top_end - y) / (top_end * 0.55), 0, 1)
    bot = np.clip((y - bot_start) / px(260), 0, 1)
    scrim = (1 - 0.72 * np.maximum(top, bot) ** 1.3)[:, :, None]

    HB = L["hist"]
    bin_min, ticks = _hist_scale(tmax)
    nb = math.ceil(tmax / 60 / bin_min)
    bins = np.minimum((durs / 60 // bin_min).astype(int), nb - 1)
    full = np.bincount(bins, minlength=nb)
    f_lab = load_font("text", px(HB["label_size"]), fonts.get("text"))
    f_credit = load_font("text", px(L["credit_size"]), fonts.get("text"))
    CREDIT = "Map data \u00a9 OpenStreetMap contributors"
    hx0, hx1, htop, hbase = px(HB["x0"]), px(HB["x1"]), px(HB["top"]), px(HB["base"])
    bw = (hx1 - hx0) / nb
    span = nb * bin_min

    def col(c, a):
        return tuple(int(v * a) for v in c)

    def draw_hist(d, t, a):
        done = np.bincount(bins[durs <= t], minlength=nb)
        for i in range(nb):
            x = hx0 + i * bw
            if full[i]:   # faint ghost of the whole distribution, filled in as runs get home
                h = (hbase - htop) * full[i] / full.max()
                d.rectangle([x + 1, hbase - h, x + bw - 2, hbase], fill=col(DIM, a * 0.5))
            if done[i]:
                h = (hbase - htop) * done[i] / full.max()
                d.rectangle([x + 1, hbase - h, x + bw - 2, hbase], fill=col(HOT, a))
        d.line([hx0, hbase + px(2), hx1, hbase + px(2)], fill=col(AXIS, a), width=px(2))
        cx = hx0 + (hx1 - hx0) * min(max(t, 0) / 60 / span, 1)
        d.line([cx, htop - px(8), cx, hbase + px(2)], fill=col(WHITE, a), width=px(2))
        for m, lab in ticks:
            d.text((hx0 + (hx1 - hx0) * m / span, hbase + px(10)), lab, font=f_lab, fill=col(GREY, a), anchor="ma")
        d.text((hx0, hbase + px(44)), "finish times", font=f_lab, fill=col(GREY, a))

    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{W}x{H}", "-r", str(FPS / step), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                           "-crf", str(crf), "-preset", "slow", "-movflags", "+faststart", out], stdin=subprocess.PIPE)
    for f, path in zip(nums, frames):
        im = Image.open(path).convert("RGB")
        im = Image.fromarray((np.asarray(im, np.float32) * scrim).astype(np.uint8))
        d = ImageDraw.Draw(im)
        a = min(1.0, f / 30)   # fade the text in over the first second
        d.text((M, px(L["title_y"])), title, font=f_title, fill=col(WHITE, a))
        d.text((M, sub_y), subtitle, font=f_sub, fill=col(GREY, a))
        t = run_time(f)
        h, rem = divmod(int(t), 3600)
        clock = f"{h}:{rem // 60:02d}:{rem % 60:02d}"
        running = int((durs > t).sum()) if f >= INTRO else len(durs)
        status = f"{running} still running" if running else "all home"
        draw_hist(d, t if f >= INTRO else -1, a)
        d.text((M, px(L["credit_y"])), CREDIT, font=f_credit, fill=col(AXIS, a))
        d.text((W - M - d.textlength(clock, font=f_clock), px(L["clock_y"])), clock, font=f_clock, fill=col(WHITE, a))
        d.text((W - M - d.textlength(status, font=f_sub), px(L["status_y"])), status, font=f_sub, fill=col(GREY, a))
        ff.stdin.write(im.tobytes())
    ff.stdin.close()
    if ff.wait():
        raise SystemExit("ffmpeg failed")
    im.save(os.path.splitext(out)[0] + "_final.png")
    log(f"wrote {out} ({len(frames)} frames)")
