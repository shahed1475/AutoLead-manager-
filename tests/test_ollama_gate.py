"""Concurrent LLM calls must reach Ollama one at a time (small-GPU default)."""
import asyncio

import pytest

from backend import ai_brain


@pytest.mark.asyncio
async def test_ollama_calls_are_serialised(monkeypatch):
    active = peak = 0

    class FakeResp:
        status_code = 200
        text = ""
        content = b'{"response": "ok"}'
        def json(self): return {"response": "ok"}
        def raise_for_status(self): pass

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            nonlocal active, peak
            active += 1; peak = max(peak, active)
            await asyncio.sleep(0.02)
            active -= 1
            return FakeResp()

    monkeypatch.setattr(ai_brain.httpx, "AsyncClient", FakeClient)
    cfg = {"base_url": "http://x", "model": "m", "timeout": 5}
    await asyncio.gather(*(ai_brain._call_ollama_raw("p", cfg, 0.1, 5) for _ in range(4)))
    assert peak == 1
