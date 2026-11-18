"""Overlay the title, clock and finish-time histogram on the rendered frames and encode an MP4, and
crop still renders to the map.

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

from . import styles, timewarp

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

# colours come from styles.TEXT; positions in px at 1080 x 1920; the map is framed between y = 461 and y = 1363 by the camera (MAP_BAND).
# sub_gap: from the title baseline to the top of the subtitle line
LAYOUT = dict(title_size=120, title_y=232, sub_size=40, sub_gap=40, clock_size=100, clock_y=1392,
              status_y=1502, credit_y=1650, credit_size=22, margin=72, scrim_top=476,
              hist=dict(x0=72, x1=600, top=1392, base=1512, label_size=28))
# 4:5 version for feeds (LinkedIn, Instagram): crop (top, height) rows out of the 9:16 frame, in the same
# 1080-wide units, and pack the text into the shorter frame. The crop suits maps that are about as wide as
# they are tall; check the result if your routes reach far north or south.
FEED_LAYOUT = dict(LAYOUT, crop=(358, 1350), title_y=70, clock_y=1010, status_y=1120, credit_y=1240, scrim_top=290,
                   hist=dict(LAYOUT["hist"], top=1010, base=1130))
# 9:16 for YouTube Shorts, Reels and TikTok, whose buttons cover the right edge and whose caption covers the
# bottom fifth. The frame moves up (rows past the bottom come out black) and the text keeps clear of both.
SHORTS_LAYOUT = dict(LAYOUT, crop=(170, 1920), title_y=190, clock_y=1205, clock_size=90, status_y=1305,
                     credit_y=1440, scrim_top=420, margin_r=190,
                     hist=dict(LAYOUT["hist"], x1=470, top=1205, base=1320))
LAYOUTS = {"vertical": LAYOUT, "feed": FEED_LAYOUT, "shorts": SHORTS_LAYOUT}
MAP_BAND = (461, 1363)   # from Y_LO, Y_HI in scene.py
CREDIT = "Map data \u00a9 OpenStreetMap contributors"


def load_font(kind, size, override=None):
    if override:   # "path" or "path#index" to pick a face inside a .ttc collection
        path, _, idx = override.rpartition("#") if "#" in override else (override, "", "0")
        cands = [(path, int(idx))]
    else:
        cands = FONTS[kind]
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


def encode(cfg, routes, frames_dir, out, title, subtitle, fonts=None, crf=16, credit=None, layout=None, log=print):
    """Draw the overlay in the colours of the style the frames were rendered in (cfg["style"]) and encode."""
    fonts = fonts or {}
    P = styles.TEXT[cfg.get("style", "dark")]
    WHITE, GREY, HOT, DIM, AXIS = P["main"], P["sub"], P["hot"], P["dim"], P["axis"]
    FPS, INTRO, RUN_FRAMES = cfg["fps"], cfg["intro"], cfg["run_frames"]
    durs = np.array([r["duration"] for r in routes])
    tmax = float(durs.max())

    def run_time(f):
        return timewarp.run_time(f, INTRO, RUN_FRAMES, cfg["time_k"], tmax, cfg["ramp"])

    frames = sorted(glob.glob(os.path.join(frames_dir, "f_*.png")))
    nums = [int(re.search(r"(\d+)\.png$", p).group(1)) for p in frames]
    step = nums[1] - nums[0] if len(nums) > 1 else 1
    L = layout or LAYOUT
    W, H = Image.open(frames[0]).size
    src_h, crop = H, None
    if "crop" in L:
        top, h = (round(v * W / 1080) for v in L["crop"])
        crop, H = (0, top, W, top + h), h
    k = W / 1080
    px = lambda v: round(v * k)  # noqa: E731
    f_title = load_font("title", px(L["title_size"]), fonts.get("title"))
    f_sub = load_font("text", px(L["sub_size"]), fonts.get("text"))
    f_clock = load_font("clock", px(L["clock_size"]), fonts.get("clock"))
    M = px(L["margin"])
    MR = px(L.get("margin_r", L["margin"]))   # right margin
    # shrink the title until it fits on one line
    while f_title.getlength(title) > W - 2 * M and f_title.size > 20:
        f_title = load_font("title", f_title.size - 4, fonts.get("title"))
    # the subtitle follows the title's baseline, so it stays close when a long title is shrunk
    sub_y = px(L["title_y"]) + f_title.getbbox("H")[3] + px(L["sub_gap"])

    # gradients towards the background behind the text so routes passing underneath don't hurt legibility;
    # keep is the share of the frame left, the rest is the scrim colour
    y = np.arange(H, dtype=np.float32)[:, None]
    top_end, bot_start = px(L["scrim_top"]), px(L["clock_y"]) - px(90)   # scrim ends where it always has, below the subtitle
    top = np.clip((top_end - y) / (top_end * 0.55), 0, 1)
    bot = np.clip((y - bot_start) / px(260), 0, 1)
    keep = (1 - P["scrim_k"] * np.maximum(top, bot) ** 1.3)[:, :, None]
    if crop and crop[3] > src_h:   # the crop runs past the bottom of the render: fade into the empty rows
        edge = src_h - crop[1]
        keep *= np.clip((edge - y) / px(240), 0, 1)[:, :, None]
    scrim = (1 - keep) * np.array(P["scrim"], np.float32)

    HB = L["hist"]
    bin_min, ticks = _hist_scale(tmax)
    nb = math.ceil(tmax / 60 / bin_min)
    bins = np.minimum((durs / 60 // bin_min).astype(int), nb - 1)
    full = np.bincount(bins, minlength=nb)
    f_lab = load_font("text", px(HB["label_size"]), fonts.get("text"))
    f_cap = load_font("text", px(HB["label_size"] * 0.8), fonts.get("text"))
    f_credit = load_font("text", px(L["credit_size"]), fonts.get("text"))
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
                d.rectangle([x + 1, hbase - h, x + bw - 2, hbase], fill=col(DIM, a))
            if done[i]:
                h = (hbase - htop) * done[i] / full.max()
                d.rectangle([x + 1, hbase - h, x + bw - 2, hbase], fill=col(HOT, a))
        d.line([hx0, hbase + px(2), hx1, hbase + px(2)], fill=col(AXIS, a), width=px(2))
        cx = hx0 + (hx1 - hx0) * min(max(t, 0) / 60 / span, 1)
        d.line([cx, htop - px(8), cx, hbase + px(2)], fill=col(WHITE, a), width=px(2))
        for m, lab in ticks:
            d.text((hx0 + (hx1 - hx0) * m / span, hbase + px(10)), "0" if m == 0 else lab, font=f_lab,
                   fill=col(GREY, a), anchor="ma")
        # axis title in spaced capitals, so it reads as a label rather than another tick
        caps(d, hx0, "FINISH TIME", col(GREY, a))

    def caps(d, x, text, fill, right=False):
        """Small capitals with extra letter spacing, on the caption line below the histogram."""
        widths = [d.textlength(ch, font=f_cap) + px(3) for ch in text]
        if right:
            x -= sum(widths) - px(3)
        for ch, w in zip(text, widths):
            d.text((x, hbase + px(54)), ch, font=f_cap, fill=fill)
            x += w

    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                           "-s", f"{W}x{H}", "-r", str(FPS / step), "-i", "-",
                           # convert and tag as BT.709: untagged HD video is shown as BT.709, which shifts reds
                           "-vf", "scale=out_color_matrix=bt709:out_range=tv,"
                           "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
                           "-c:v", "libx264", "-pix_fmt", "yuv420p",
                           "-crf", str(crf), "-preset", "slow", "-movflags", "+faststart", out], stdin=subprocess.PIPE)
    for f, path in zip(nums, frames):
        im = Image.open(path).convert("RGB")
        if crop:
            im = im.crop(crop)
        im = Image.fromarray((np.asarray(im, np.float32) * keep + scrim).astype(np.uint8))
        d = ImageDraw.Draw(im)
        a = 1.0   # text at full strength from frame 0, which feeds use as the thumbnail
        d.text((M, px(L["title_y"])), title, font=f_title, fill=col(WHITE, a))
        d.text((M, sub_y), subtitle, font=f_sub, fill=col(GREY, a))
        t = run_time(f)
        h, rem = divmod(int(t), 3600)
        clock = f"{h}:{rem // 60:02d}:{rem % 60:02d}"
        running = int((durs > t).sum()) if f >= INTRO else len(durs)
        status = f"{running} still running" if running else "all home"
        draw_hist(d, t if f >= INTRO else -1, a)
        d.text((M, px(L["credit_y"])), CREDIT, font=f_credit, fill=col(AXIS, a))
        if credit:
            d.text((M, px(L["credit_y"] + 34)), credit, font=f_credit, fill=col(AXIS, a))
        d.text((W - MR - d.textlength(clock, font=f_clock), px(L["clock_y"])), clock, font=f_clock, fill=col(WHITE, a))
        d.text((W - MR - d.textlength(status, font=f_sub), px(L["status_y"])), status, font=f_sub, fill=col(GREY, a))
        caps(d, W - MR, "CLOCK SPEEDS UP", col(GREY, a), right=True)
        ff.stdin.write(im.tobytes())
    ff.stdin.close()
    if ff.wait():
        raise SystemExit("ffmpeg failed")
    im.save(os.path.splitext(out)[0] + "_final.png")
    log(f"wrote {out} ({len(frames)} frames)")


def still(src, out, fonts=None):
    """Crop a still render to the map (the band the camera keeps clear between the title and the clock,
    plus a little air) and add the map credit, dark on light maps and light on dark ones."""
    fonts = fonts or {}
    im = Image.open(src).convert("RGB")
    W, H = im.size
    k = min(W / 1080, H / 1920)   # so the credit stays below the map band on frames wider than 9:16
    air = 60 * H / 1920
    im = im.crop((0, round(MAP_BAND[0] * H / 1920 - air), W, round(MAP_BAND[1] * H / 1920 + air)))
    M = round(LAYOUT["margin"] * k)
    corner = np.asarray(im.crop((im.width - 6 * M, im.height - M, im.width, im.height)), np.float32).mean()
    d = ImageDraw.Draw(im)
    f = load_font("text", round(LAYOUT["credit_size"] * k), fonts.get("text"))
    d.text((im.width - M, im.height - M // 2), CREDIT, font=f, fill=(120, 120, 120) if corner > 128 else styles.TEXT["dark"]["axis"],
           anchor="rs")
    im.save(out)
