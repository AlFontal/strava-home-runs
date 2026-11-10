"""Frame <-> run-clock mapping shared by the Blender scene and the overlay encoder.

The clock is stopped until `intro`, accelerates from zero over `ramp` frames (smoothstep speed),
then follows an exponential curve (k > 0 starts slow, ends fast; k = 0 is linear) that reaches the
longest run's duration `ramp / 2` frames after `intro + run_frames`.
"""
import math


def _progress(f, intro, run_frames, ramp):
    """Effective frames elapsed (0 .. run_frames), easing in from standstill."""
    x = f - intro
    if x <= 0:
        return 0.0
    if ramp > 0 and x < ramp:
        s = x / ramp
        g = ramp * (s ** 3 - s ** 4 / 2)        # integral of smoothstep speed
    else:
        g = x - ramp / 2 if ramp > 0 else x
    return min(g, run_frames)


def run_time(f, intro, run_frames, k, tmax, ramp=0):
    u = _progress(f, intro, run_frames, ramp) / run_frames
    return tmax * (u if k == 0 else (math.exp(k * u) - 1) / (math.exp(k) - 1))


def frame_at(t, intro, run_frames, k, tmax, ramp=0):
    """Inverse of run_time (first frame at which the clock reaches t)."""
    y = min(max(t / tmax, 0.0), 1.0)
    u = y if k == 0 else math.log(1 + y * (math.exp(k) - 1)) / k
    g = u * run_frames
    if ramp > 0 and g < ramp / 2:                 # inside the ease-in: solve s^3 - s^4/2 = g/ramp
        lo, hi = 0.0, 1.0
        for _ in range(50):
            m = (lo + hi) / 2
            lo, hi = (m, hi) if ramp * (m ** 3 - m ** 4 / 2) < g else (lo, m)
        return intro + hi * ramp
    return intro + g + (ramp / 2 if ramp > 0 else 0)


def end_frame_of_clock(intro, run_frames, ramp=0):
    return intro + run_frames + (ramp / 2 if ramp > 0 else 0)
