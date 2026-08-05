import pytest

from backend.intelligence import orchestrator as orch_module
from backend.intelligence.base import AgentResult, EvidenceItem

pytestmark = pytest.mark.asyncio


class _StubAgent:
    def __init__(self, result: AgentResult):
        self._result = result

    async def run(self, lead, campaign=None):
        return self._result


class _CrashingAgent:
    async def run(self, lead, campaign=None):
        raise RuntimeError("simulated crash")


async def test_qualified_lead_runs_full_pipeline(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Acme Dental", "email": "hi@acmedental.co"})
    lead = await db.get_lead_by_id(lead_id)

    monkeypatch.setattr(orch_module, "_qualification_agent", _StubAgent(AgentResult(
        status="ok",
        data={"qualification_status": "QUALIFIED", "qualification_confidence": 0.9},
        evidence=[EvidenceItem("qualification_status", "heuristic", None, "all checks passed")],
        confidence=0.9,
    )))
    monkeypatch.setattr(orch_module, "_company_research_agent", _StubAgent(AgentResult(
        status="ok",
        data={"industry": "Dental Care"},
        evidence=[EvidenceItem("industry", "ai_inference", "https://acmedental.co", None)],
        confidence=1.0,
    )))

    result = await orch_module.run_research_pipeline(lead)
    assert result["status"] == "DONE"

    profile = await db.get_company_profile(lead_id)
    assert profile["status"] == "DONE"
    assert profile["qualification_status"] == "QUALIFIED"
    assert profile["industry"] == "Dental Care"

    evidence = await db.get_research_evidence(profile["id"])
    assert len(evidence) == 2


async def test_rejected_lead_short_circuits_before_research(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Spammy Co"})
    lead = await db.get_lead_by_id(lead_id)

    research_agent = _StubAgent(AgentResult(status="ok", data={"industry": "should not run"}))
    monkeypatch.setattr(orch_module, "_qualification_agent", _StubAgent(AgentResult(
        status="rejected",
        data={"qualification_status": "REJECTED", "qualification_confidence": 0.1},
        evidence=[], confidence=0.1, reason="missing business name",
    )))
    monkeypatch.setattr(orch_module, "_company_research_agent", research_agent)

    result = await orch_module.run_research_pipeline(lead)
    assert result["status"] == "REJECTED"

    profile = await db.get_company_profile(lead_id)
    assert profile["status"] == "REJECTED"
    assert profile["industry"] is None  # research agent never ran


async def test_qualification_result_survives_a_research_crash(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Crashy Co", "email": "hi@crashyco.io"})
    lead = await db.get_lead_by_id(lead_id)

    monkeypatch.setattr(orch_module, "_qualification_agent", _StubAgent(AgentResult(
        status="ok",
        data={"qualification_status": "QUALIFIED", "qualification_confidence": 0.9},
        evidence=[], confidence=0.9,
    )))
    monkeypatch.setattr(orch_module, "_company_research_agent", _CrashingAgent())

    result = await orch_module.run_research_pipeline(lead)
    assert result["status"] == "FAILED"

    profile = await db.get_company_profile(lead_id)
    assert profile["qualification_status"] == "QUALIFIED"  # progressive persistence — survived the crash
    assert profile["status"] == "FAILED"


async def test_run_pending_research_scoped_to_explicit_lead_ids(clean_db, monkeypatch):
    db = clean_db
    lead_id_1 = await db.create_lead({"business_name": "New Lead Co"})
    lead_id_2 = await db.create_lead({"business_name": "Already Pending Co"})
    await db.upsert_company_profile(lead_id_2, {"status": "PENDING"})
    # a third, unrelated lead must NOT be touched by this scoped call
    lead_id_3 = await db.create_lead({"business_name": "Unrelated Co"})

    monkeypatch.setattr(orch_module, "_qualification_agent", _StubAgent(AgentResult(
        status="ok", data={"qualification_status": "QUALIFIED", "qualification_confidence": 0.9},
        evidence=[], confidence=0.9,
    )))
    monkeypatch.setattr(orch_module, "_company_research_agent", _StubAgent(AgentResult(
        status="ok", data={"industry": "Test"}, evidence=[], confidence=1.0,
    )))

    outcome = await orch_module.run_pending_research(lead_ids=[lead_id_1, lead_id_2])
    assert outcome["processed"] == 2

    for lid in (lead_id_1, lead_id_2):
        profile = await db.get_company_profile(lid)
        assert profile["status"] == "DONE"

    assert await db.get_company_profile(lead_id_3) is None  # untouched


async def test_resume_after_interruption(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Resumed Co"})
    await db.upsert_company_profile(lead_id, {"status": "RESEARCHING"})

    await db.init_db()  # simulates a process restart -> sweep resets RESEARCHING to PENDING

    monkeypatch.setattr(orch_module, "_qualification_agent", _StubAgent(AgentResult(
        status="ok", data={"qualification_status": "QUALIFIED", "qualification_confidence": 0.9},
        evidence=[], confidence=0.9,
    )))
    monkeypatch.setattr(orch_module, "_company_research_agent", _StubAgent(AgentResult(
        status="ok", data={"industry": "Resumed"}, evidence=[], confidence=1.0,
    )))

    outcome = await orch_module.run_pending_research()
    assert outcome["processed"] == 1
    profile = await db.get_company_profile(lead_id)
    assert profile["status"] == "DONE"


async def test_run_pending_research_returns_zero_when_nothing_pending(clean_db):
    outcome = await orch_module.run_pending_research()
    assert outcome == {"processed": 0, "results": []}
