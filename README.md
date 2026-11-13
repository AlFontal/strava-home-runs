# strava-home-runs

[![tests](https://github.com/AlFontal/strava-home-runs/actions/workflows/tests.yml/badge.svg)](https://github.com/AlFontal/strava-home-runs/actions/workflows/tests.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Turns GPX, TCX or FIT files (from Strava, Garmin or any other GPS source) into a 3D video of every run that started within a chosen radius of one point. It generalizes the script I used to animate five years of runs that started from my home.

<p align="center"><img src="docs/preview.gif" alt="Animation of 289 runs leaving the same point in Barcelona, each at its own pace, with a clock and a histogram of finish times" width="320"></p>

All runs leave at the same moment and each one moves at its real pace. Routes you run often glow brighter, like a heat map. A clock, a "still running" counter and a histogram of finish times fill in as runners get home. The runner marker can be a cut-out of your face.

The map uses OpenStreetMap streets and AWS Terrain Tiles elevation and is rendered in Blender. No API keys are needed.

## Requirements

- [pixi](https://pixi.sh), which installs Python, the Python packages and ffmpeg into a local environment.
- [Blender](https://www.blender.org/download/) 5.0 or newer (tested with 5.2), installed separately.

<details>
<summary>macOS (Homebrew)</summary>

```bash
brew install pixi
brew install --cask blender
```

</details>

<details>
<summary>Linux</summary>

```bash
curl -fsSL https://pixi.sh/install.sh | sh
sudo snap install blender --classic   # or the tarball from blender.org
```

</details>

<details>
<summary>Windows (PowerShell)</summary>

```powershell
winget install prefix-dev.pixi
winget install BlenderFoundation.Blender
```

</details>

The tool looks for Blender on your `PATH` and in `/Applications/Blender.app`; `--blender /path/to/blender` points it elsewhere. [uv](https://docs.astral.sh/uv/) also works (`uv run homeruns ...`) if ffmpeg is on your `PATH`.

## Getting your files

- **Strava:** Settings → My Account → Download or Delete Your Account → Request your archive. Use the `activities/` folder from the zip.
- **Garmin Connect:** Account → Data Management → Export Your Data. The FIT files are in zip files inside the export; unzip those too.

The tool reads `.gpx`, `.tcx` and `.fit` files, gzipped or not, and searches folders recursively.

## Usage

Run it from this folder. The first `pixi run` sets up the environment.

```bash
pixi run homeruns ~/Downloads/strava_export/activities --face me.png --place Lisbon --preview
```

`--preview` renders a half-size version in a few minutes. When it looks right, run the same command without `--preview` for the full 1440×2560 video. On an Apple Silicon Mac that takes about 1.5 hours (5 s per frame).

`--face` takes a PNG or WebP of a face on a transparent background. On macOS and iOS, "Copy Subject" in Photos makes one. The face is tinted orange; `--tint 0` keeps its colours. Without `--face`, each runner is an orange dot.

`pixi run homeruns --help` lists all options.

<details>
<summary><b>Options</b></summary>

| Option | Default | |
|---|---|---|
| `--title`, `--subtitle` | `HOME RUNS since <year>`, `<n> runs, <total> km` | Text at the top; can use `{runs}`, `{km}`, `{first_year}`, `{last_year}` |
| `--place` | | Added to the default subtitle |
| `--radius` | 200 | Metres from home a run must start within |
| `--home` | auto | `lat,lon` of home |
| `--since`, `--until` | | Date range, `YYYY-MM-DD` |
| `--sport` | `run` | Keep tracks whose type contains this; `any` keeps all |
| `--seconds` | 30 | Video seconds until the longest run finishes |
| `--size` | `1440x2560` | Output size; the layout is made for 9:16 |
| `--privacy-radius` | 0 | Cut this many metres around home from every track |
| `--no-sea` | | Don't treat land at sea level as sea (needed in places like the Netherlands) |
| `--line-strength` | auto | Brightness of a single route |
| `--crf` | 16 | Video quality; higher gives a smaller file |
| `--font-title`, `--font-text`, `--font-clock` | system fonts | TTF/OTF files |
| `--keep-frames` | | Keep the rendered PNGs in the work folder |
| `--overlay-only` | | Redraw the text on kept frames and encode, without Blender |

</details>

<details>
<summary><b>Pipeline details</b></summary>

1. Reads every track and keeps the runs (`--sport`). Files without a type are kept.
2. Finds home, the place where most runs start (or `--home lat,lon`), and keeps the runs that start within `--radius` metres.
3. Cleans the GPS: drops spikes faster than `--max-speed` (7 m/s), cuts a track where the signal is lost for good (in the metro, for example) and shortens long pauses.
4. Downloads elevation and streets for the area and caches them in `--work` (default `.homeruns/`).
5. Renders the scene in Blender, adds the text overlay and writes `home_runs.mp4` and `home_runs_final.png`.

</details>

<details>
<summary><b>Title and overlay details</b></summary>

The title and subtitle can use `{runs}`, `{km}` (total distance), `{first_year}` and `{last_year}`, with Python format specs after a colon:

```bash
pixi run homeruns ~/Downloads/strava_export/activities --title "All my runs from home since {first_year}" \
    --subtitle "{runs} runs, {km:.0f} km, Lisbon"
```

The distance is measured on the cleaned tracks after `--privacy-radius`, so it can be a little lower than Strava's total.


<b>Changing the text without rendering again</b>

The text, clock and histogram are drawn after Blender renders the frames. Render with `--keep-frames` (a few GB at full size), and `--overlay-only` can redo the text in a few minutes without Blender:

```bash
pixi run homeruns ~/Downloads/strava_export/activities --face me.png --place Lisbon --keep-frames
pixi run homeruns --overlay-only --title "MY HOME RUNS" --place Lisbon
```

`--overlay-only` takes no track files. It reads everything from the work folder, so pass the same `--work` (and `--preview` for preview frames). It accepts the text, font, `--out` and `--crf` options. Changes to the 3D scene, such as the map, routes, camera, marker or `--seconds`, need a full render. Frames kept before `{km}` was added don't store distances, so `{km}` can't be filled for them.

</details>

## Privacy

The video starts at your chosen location and every run leaves from it, which can show where you live. `--privacy-radius 300` cuts the first and last 300 m of every track, like Strava's privacy zones, so runners appear at the edge of the circle. With many runs the centre can still be estimated, so don't publish the video if your address must stay private.

<details>
<summary><b>How it works</b></summary>

- **Timing:** the clock starts slowly and speeds up exponentially, so the busy first minutes are readable and the long runs don't drag.
- **Camera:** at every frame, it finds the closest position at which everything drawn so far fits between the title and the clock.
- **Heat:** routes render additively in a separate view layer, and the compositor maps the sum onto a heat colour ramp.
- **Marker:** each face is a camera-facing card at the head of its route.

The code is in `homeruns/`:

| File | Role |
|---|---|
| `tracks.py` | Reads GPX/TCX/FIT, finds home, cleans GPS |
| `basemap.py` | Downloads elevation and streets |
| `prepare.py` | Builds the street texture and route points |
| `scene.py` | Builds the scene in Blender |
| `render_setup.py` | Render settings |
| `overlay.py` | Text, histogram and encoding |
| `timewarp.py` | Clock curve |
| `face.py` | Face and dot marker sprites |
| `__main__.py` | Command line and pipeline |

Tests for track reading, cleaning and the clock curve are in `tests/` and run with `pixi run test` (or `uv run pytest`). They don't include Blender.

</details>

## Credits

- Map data © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors (ODbL). The video carries this credit.
- Elevation from [AWS Terrain Tiles](https://registry.opendata.aws/terrain-tiles/) (SRTM, GMTED, NED and other sources).
- Strava is a trademark of Strava, Inc. and Garmin is a trademark of Garmin Ltd. This project is not affiliated with or endorsed by either.

## License

MIT
