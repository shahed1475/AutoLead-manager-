import asyncio

import pytest

from backend.research_agent.pacing import (
    PACING_PROFILES,
    PacingController,
    instant_controller,
    resolve_profile,
)

pytestmark = pytest.mark.asyncio


class _RecordingSleep:
    def __init__(self):
        self.calls = []

    async def __call__(self, seconds):
        self.calls.append(seconds)


def test_resolve_profile_known_names():
    assert resolve_profile("fast").name == "fast"
    assert resolve_profile("standard").name == "standard"
    assert resolve_profile("deliberate").name == "deliberate"


def test_resolve_profile_unknown_falls_back_to_deliberate():
    assert resolve_profile("bogus").name == "deliberate"
    assert resolve_profile(None).name == "deliberate"
    assert resolve_profile("DELIBERATE").name == "deliberate"  # case-insensitive


async def test_wait_samples_within_the_profiles_range():
    sleep = _RecordingSleep()
    ctl = PacingController(PACING_PROFILES["standard"], sleep_fn=sleep)
    lo, hi = PACING_PROFILES["standard"].range_for("after_scroll")
    duration = await ctl.wait("after_scroll")
    assert lo <= duration <= hi
    assert sleep.calls == [duration] or (duration == 0.0 and sleep.calls == [])


async def test_wait_unknown_op_is_a_safe_noop():
    sleep = _RecordingSleep()
    ctl = PacingController(PACING_PROFILES["deliberate"], sleep_fn=sleep)
    duration = await ctl.wait("not_a_real_op")
    assert duration == 0.0
    assert sleep.calls == []


async def test_settle_returns_settled_once_content_stabilises():
    class _Page:
        def __init__(self):
            self._n = 0

        async def evaluate(self, js):
            if "readyState" in js:
                return "complete"
            self._n += 1
            return 100  # constant text length -> stable immediately

    ctl = PacingController(PACING_PROFILES["fast"], sleep_fn=_RecordingSleep())
    result = await ctl.settle(_Page(), cap_s=1.0)
    assert result == "settled"


async def test_settle_times_out_when_content_never_stabilises():
    class _GrowingPage:
        def __init__(self):
            self._len = 0

        async def evaluate(self, js):
            if "readyState" in js:
                return "complete"
            self._len += 50
            return self._len  # always growing -> never stable

    ctl = PacingController(PACING_PROFILES["fast"], sleep_fn=_RecordingSleep())
    result = await ctl.settle(_GrowingPage(), cap_s=0.05)
    assert result == "timeout"


async def test_settle_handles_evaluate_errors_gracefully():
    class _BrokenPage:
        async def evaluate(self, js):
            raise RuntimeError("page closed")

    ctl = PacingController(PACING_PROFILES["fast"], sleep_fn=_RecordingSleep())
    result = await ctl.settle(_BrokenPage(), cap_s=0.2)
    assert result == "timeout"


async def test_instant_controller_never_really_sleeps():
    ctl = instant_controller()
    # Even the deliberate-sized upper bound would be seconds; instant_controller
    # must return near-immediately regardless of profile because its sleep_fn
    # is a no-op.
    import time
    started = time.monotonic()
    await ctl.wait("between_leads")
    assert time.monotonic() - started < 0.05
