"""Phase F — automation Research Agent section: queue summary on /status."""
import pytest
from httpx import ASGITransport, AsyncClient

from backend import database as db

pytestmark = pytest.mark.asyncio


async def _client():
    from backend.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_status_exposes_research_queue_for_automation_leads(clean_db):
    dc = clean_db
    await dc.get_automation_state()
    a = await dc.create_lead({"business_name": "A", "phone": "5550001", "source_type": "automation"})
    b = await dc.create_lead({"business_name": "B", "phone": "5550002", "source_type": "automation"})
    m = await dc.create_lead({"business_name": "M", "phone": "5550003", "source_type": "manual"})
    await dc.update_lead(a, {"research_status": "QUEUED"})
    await dc.update_lead(b, {"research_status": "COMPLETED"})
    await dc.update_lead(m, {"research_status": "QUEUED"})   # manual — must not count

    async with await _client() as c:
        body = (await c.get("/api/automation/status")).json()
    assert body["research_queue"] == {"queued": 1, "researching": 0, "completed": 1, "failed": 0}
