import httpx
import pytest
import respx

from backend.intelligence.qualification_agent import QualificationAgent

pytestmark = pytest.mark.asyncio


async def test_qualifies_a_complete_lead():
    lead = {
        "business_name": "Acme Dental",
        "email": "info@acmedental.co",
        "phone": "+15551234567",
        "website": "https://acmedental.co",
        "niche": "dentist",
        "city": "Metropolis",
    }
    campaign = {"niche": "dentist", "city": "Metropolis"}

    with respx.mock:
        respx.head("https://acmedental.co").mock(return_value=httpx.Response(200))
        agent = QualificationAgent()
        result = await agent.run(lead, campaign)

    assert result.status == "ok"
    assert result.data["qualification_status"] == "QUALIFIED"
    assert result.confidence >= 0.5


async def test_rejects_lead_with_no_name_or_contact():
    lead = {"business_name": "", "email": None, "phone": None, "website": None}
    agent = QualificationAgent()
    result = await agent.run(lead, None)

    assert result.status == "rejected"
    assert result.data["qualification_status"] == "REJECTED"
    assert "missing business name" in result.reason
    assert "no email, phone, or website" in result.reason


async def test_rejects_unreachable_website():
    lead = {
        "business_name": "Beta LLC",
        "email": "hi@betallc.io",
        "website": "https://betallc.io",
    }
    with respx.mock:
        respx.head("https://betallc.io").mock(side_effect=httpx.ConnectError("refused"))
        respx.get("https://betallc.io").mock(side_effect=httpx.ConnectError("refused"))
        agent = QualificationAgent()
        result = await agent.run(lead, None)

    assert "website unreachable" in result.reason


async def test_head_fails_but_get_succeeds():
    """When HEAD raises an exception but GET succeeds, lead should not be penalized for unreachable website."""
    lead = {
        "business_name": "Charlie Corp",
        "email": "info@charliecorp.io",
        "website": "https://charliecorp.io",
    }
    campaign = None

    with respx.mock:
        # HEAD raises (common for servers that don't support HEAD properly)
        respx.head("https://charliecorp.io").mock(side_effect=httpx.RemoteProtocolError("server doesn't support HEAD"))
        # GET succeeds
        respx.get("https://charliecorp.io").mock(return_value=httpx.Response(200))
        agent = QualificationAgent()
        result = await agent.run(lead, campaign)

    # Should qualify despite HEAD failure, since GET succeeded
    assert result.status == "ok"
    assert result.data["qualification_status"] == "QUALIFIED"
    assert "website unreachable" not in (result.reason or "")


async def test_niche_mismatch_recorded_in_reason():
    lead = {
        "business_name": "Gamma Cafe",
        "email": "hi@gammacafe.io",
        "phone": "+15559876543",
        "niche": "cafe",
    }
    campaign = {"niche": "dentist", "city": "Metropolis"}
    agent = QualificationAgent()
    result = await agent.run(lead, campaign)

    assert "niche mismatch" in (result.reason or "")


async def test_missing_campaign_skips_niche_and_location_checks():
    lead = {
        "business_name": "Standalone Co",
        "email": "hi@standalone.io",
        "phone": "+15551112222",
    }
    agent = QualificationAgent()
    result = await agent.run(lead, None)

    assert result.status == "ok"
    assert result.data["qualification_status"] == "QUALIFIED"
    assert result.reason is None
