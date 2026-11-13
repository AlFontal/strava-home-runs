import pytest

from homeruns.timewarp import end_frame_of_clock, frame_at, run_time

CLOCK = dict(intro=30, run_frames=600, tmax=3600.0)


@pytest.mark.parametrize("k", [0, 2.5])
@pytest.mark.parametrize("ramp", [0, 60])
def test_frame_at_inverts_run_time(k, ramp):
    for f in [31, 50, 89, 200, 600]:           # with ramp=60, the first three fall in the ease-in
        t = run_time(f, k=k, ramp=ramp, **CLOCK)
        assert frame_at(t, k=k, ramp=ramp, **CLOCK) == pytest.approx(f, abs=1e-6)


@pytest.mark.parametrize("ramp", [0, 60])
def test_clock_starts_after_intro_and_ends_at_tmax(ramp):
    end = end_frame_of_clock(CLOCK["intro"], CLOCK["run_frames"], ramp)
    assert run_time(CLOCK["intro"], k=2.5, ramp=ramp, **CLOCK) == 0
    assert run_time(end - 1, k=2.5, ramp=ramp, **CLOCK) < CLOCK["tmax"]
    assert run_time(end, k=2.5, ramp=ramp, **CLOCK) == pytest.approx(CLOCK["tmax"])
