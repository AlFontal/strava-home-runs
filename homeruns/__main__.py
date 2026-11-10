"""strava-home-runs: animate every run that started from home, each at its own pace, in 3D.

  homeruns <gpx files or folders> [--face me.png] [--preview]

See README.md for all options.
"""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import date

import numpy as np

from . import basemap, face, overlay, prepare, timewarp, tracks

HERE = os.path.dirname(os.path.abspath(__file__))
FPS, INTRO, OUTRO, RAMP, TIME_K = 30, 45, 120, 60, 2.3


def parse_args(argv):
    p = argparse.ArgumentParser(prog="homeruns", description=__doc__.splitlines()[0],
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("inputs", nargs="+", help="GPX/TCX files (optionally .gz) or folders containing them")
    p.add_argument("--face", help="transparent PNG/WebP of a face to use as the runner marker (default: a dot)")
    p.add_argument("--tint", type=float, default=0.55, help="orange tint of the face, 0 (none) to 1 (full)")
    p.add_argument("--title", help='title, default "HOME RUNS since <first year>" (or "HOME RUNS <year>")')
    p.add_argument("--subtitle", help='subtitle, default "<n> runs between <first year> and <last year>"')
    p.add_argument("--place", help='appended to the default subtitle, e.g. "Lisbon"')
    p.add_argument("--radius", type=float, default=200, help="runs must start within this many metres of home")
    p.add_argument("--home", help='"lat,lon" of home (default: the place where most runs start)')
    p.add_argument("--sport", default="run", help='keep tracks whose type contains this ("any" keeps all)')
    p.add_argument("--since", type=date.fromisoformat, help="first date to include, YYYY-MM-DD")
    p.add_argument("--until", type=date.fromisoformat, help="last date to include, YYYY-MM-DD")
    p.add_argument("--privacy-radius", type=float, default=0,
                   help="cut the first and last N metres around home from every track (hides the exact start)")
    p.add_argument("--seconds", type=float, default=30, help="video seconds for the longest run to finish")
    p.add_argument("--size", default="1440x2560", help="output size WxH (the layout is made for 9:16)")
    p.add_argument("--preview", action="store_true", help="quick low-res check: half size, every 2nd frame")
    p.add_argument("--out", default="home_runs.mp4", help="output video")
    p.add_argument("--work", default=".homeruns", help="working folder (map cache, scene, frames)")
    p.add_argument("--blender", help="path to the Blender executable (5.0 or newer)")
    p.add_argument("--no-sea", action="store_true", help="don't treat land at sea level as sea (e.g. polders)")
    p.add_argument("--max-speed", type=float, default=7.0, help="m/s; faster GPS jumps are treated as errors")
    p.add_argument("--line-strength", type=float, help="brightness of a single route (default: from the run count)")
    p.add_argument("--crf", type=int, default=16, help="x264 quality, lower is better and bigger")
    p.add_argument("--keep-frames", action="store_true", help="keep the rendered PNG frames")
    for k in ("title", "text", "clock"):
        p.add_argument(f"--font-{k}", help=f"TTF/OTF font for the {k}")
    return p.parse_args(argv)


def find_blender(path):
    for cand in [path, shutil.which("blender"), "/Applications/Blender.app/Contents/MacOS/Blender"]:
        if cand and os.path.exists(cand):
            return cand
    raise SystemExit("Blender not found. Install Blender 5.0+ and pass --blender /path/to/blender.")


def progress(done, total, t0, width=32):
    n = int(width * done / max(total, 1))
    el = time.time() - t0
    eta = el / done * (total - done) if done else 0
    sys.stdout.write(f"\r  [{'#' * n}{'-' * (width - n)}] {done}/{total} frames  ETA {int(eta // 60)}m{int(eta % 60):02d}s ")
    sys.stdout.flush()


def main(argv=None):
    a = parse_args(argv)
    blender = find_blender(a.blender)
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not found on PATH.")
    W, H = (int(v) for v in a.size.lower().split("x"))
    work = os.path.abspath(a.work)
    os.makedirs(work, exist_ok=True)

    # ---- tracks ----
    files = tracks.find_files(a.inputs)
    if not files:
        raise SystemExit("no .gpx/.tcx files found")
    runs, skipped = [], 0
    for f in files:
        tr = tracks.read_track(f)
        if tr is None or (a.sport != "any" and tr.sport and a.sport.lower() not in tr.sport):
            skipped += 1
            continue
        d = tr.start.date()
        if (a.since and d < a.since) or (a.until and d > a.until):
            continue
        runs.append(tracks.clean(tr, a.max_speed))
    if not runs:
        raise SystemExit("no timed tracks of the requested sport/dates")
    starts = np.array([r.loc[0] for r in runs])
    home = np.array([float(v) for v in a.home.split(",")]) if a.home else tracks.find_home(starts, a.radius)
    runs = [r for r in runs if tracks.dist_to(r.loc[:1], home)[0] <= a.radius]
    print(f"{len(files)} files, {len(runs)} runs start within {a.radius:.0f} m of home"
          + (f" ({skipped} skipped: no timestamps or another sport)" if skipped else ""))
    if len(runs) < 2:
        raise SystemExit("need at least 2 runs from home; try a larger --radius or --home lat,lon")
    if a.privacy_radius > 0:
        runs = [r for r in (tracks.hide_ends(r, home, a.privacy_radius) for r in runs) if r is not None]
    runs.sort(key=lambda r: r.start)

    # ---- map ----
    allp = np.concatenate([r.loc for r in runs])
    S, N = allp[:, 0].min(), allp[:, 0].max()
    Wl, El = allp[:, 1].min(), allp[:, 1].max()
    ky, kx = tracks.metres_per_degree((S + N) / 2)
    ext_km = max((N - S) * ky, (El - Wl) * kx) / 1000
    pad = max(0.12 * ext_km, 0.4) * 1000
    S, N, Wl, El = S - pad / ky, N + pad / ky, Wl - pad / kx, El + pad / kx
    if ext_km > 30:
        print(f"warning: the runs span {ext_km:.0f} km; the map download and render will be slow. "
              "Consider --radius, --since or removing far-away runs.")
    basemap.fetch(os.path.join(work, "basemap"), S, N, Wl, El)
    WKM, HKM = prepare.prepare(os.path.join(work, "basemap"), work, runs, sea=not a.no_sea)
    if a.face:
        face.face_sprite(a.face, os.path.join(work, "face.png"), a.tint)
    else:
        face.dot_sprite(os.path.join(work, "face.png"))

    # ---- scene ----
    run_frames = int(round(a.seconds * FPS))
    frames_dir = os.path.join(work, "frames_preview" if a.preview else "frames")
    cfg = dict(work=work, res=[W, H], fps=FPS, intro=INTRO, run_frames=run_frames, outro=OUTRO, time_k=TIME_K,
               ramp=RAMP, snap_start=a.privacy_radius <= 0, sea=not a.no_sea,
               scale=float(np.clip(max(WKM, HKM) / 12, 0.3, 3.0)),
               line_strength=a.line_strength or float(np.clip(0.012 * math.sqrt(289 / len(runs)), 0.012, 0.05)),
               preview=a.preview, frames=frames_dir)
    cfg_path = os.path.join(work, "config.json")
    json.dump(cfg, open(cfg_path, "w"), indent=1)
    print("building scene in Blender")
    r = subprocess.run([blender, "-b", "--factory-startup", "-P", os.path.join(HERE, "scene.py"), "--", cfg_path],
                       capture_output=True, text=True)
    if r.returncode or not os.path.exists(os.path.join(work, "scene.blend")):
        sys.stderr.write(r.stdout[-3000:] + r.stderr[-3000:])
        raise SystemExit("Blender scene build failed")

    # ---- render ----
    shutil.rmtree(frames_dir, ignore_errors=True)
    os.makedirs(frames_dir)
    end = int(math.ceil(timewarp.end_frame_of_clock(INTRO, run_frames, RAMP))) + OUTRO
    total = math.ceil(end / 2) if a.preview else end
    print(f"rendering {total} frames" + (" (preview)" if a.preview else ""))
    logf = open(os.path.join(work, "render.log"), "w")
    proc = subprocess.Popen([blender, "-b", os.path.join(work, "scene.blend"), "-P",
                             os.path.join(HERE, "render_setup.py"), "-a", "--", cfg_path], stdout=logf, stderr=logf)
    t0 = time.time()
    while proc.poll() is None:
        progress(len(os.listdir(frames_dir)), total, t0)
        time.sleep(2)
    progress(len(os.listdir(frames_dir)), total, t0)
    print()
    if proc.returncode or not os.listdir(frames_dir):
        raise SystemExit(f"render failed, see {os.path.join(work, 'render.log')}")

    # ---- overlay + encode ----
    routes = json.load(open(os.path.join(work, "routes.json")))["routes"]
    years = sorted({r["date"][:4] for r in routes})
    title = a.title or (f"HOME RUNS {years[0]}" if len(years) == 1 else f"HOME RUNS since {years[0]}")
    if a.subtitle:
        subtitle = a.subtitle
    else:
        subtitle = f"{len(routes)} runs " + (f"in {years[0]}" if len(years) == 1 else f"between {years[0]} and {years[-1]}")
        subtitle += f", {a.place}" if a.place else ""
    out = a.out
    if a.preview:
        root, ext = os.path.splitext(out)
        out = f"{root}_preview{ext or '.mp4'}"
    fonts = {"title": a.font_title, "text": a.font_text, "clock": a.font_clock}
    overlay.encode(cfg, routes, frames_dir, out, title, subtitle, fonts, a.crf)
    if not a.keep_frames:
        shutil.rmtree(frames_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
