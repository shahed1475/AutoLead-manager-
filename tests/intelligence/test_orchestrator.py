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


class _StubAgentPainPoint:
    """Matches PainPointAgent.run's 3-positional-arg signature (lead, company_profile, evidence)."""
    def __init__(self, result: AgentResult):
        self._result = result

    async def run(self, lead, company_profile, evidence, campaign=None):
        return self._result


class _CrashingPainPointAgent:
    async def run(self, lead, company_profile, evidence, campaign=None):
        raise RuntimeError("simulated pain point agent crash")


class _StubAgentOpportunity:
    """Matches OpportunityAgent.run's 4-positional-arg signature (lead, company_profile, pain_points, evidence)."""
    def __init__(self, result: AgentResult):
        self._result = result

    async def run(self, lead, company_profile, pain_points, evidence, campaign=None):
        return self._result


class _CrashingOpportunityAgent:
    async def run(self, lead, company_profile, pain_points, evidence, campaign=None):
        raise RuntimeError("simulated opportunity agent crash")


class _StubAgentMarketing:
    """Matches MarketingAgent.run's 6-positional-arg signature
    (lead, company_profile, pain_points, opportunities, solutions, evidence)."""
    def __init__(self, result: AgentResult):
        self._result = result

    async def run(self, lead, company_profile, pain_points, opportunities, solutions, evidence, campaign=None):
        return self._result


class _CrashingMarketingAgent:
    async def run(self, lead, company_profile, pain_points, opportunities, solutions, evidence, campaign=None):
        raise RuntimeError("simulated marketing agent crash")


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


# ─────────────────────────────────────────────────────────────────────────────
# run_pain_point_analysis — separate entry point, does not touch the
# run_research_pipeline/run_pending_research tests or behavior above.
# ─────────────────────────────────────────────────────────────────────────────

async def test_pain_point_analysis_skipped_when_no_profile(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "No Profile Co"})
    lead = await db.get_lead_by_id(lead_id)

    result = await orch_module.run_pain_point_analysis(lead)
    assert result["status"] == "SKIPPED"


async def test_pain_point_analysis_skipped_when_research_not_done(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Still Researching Co"})
    await db.upsert_company_profile(lead_id, {"status": "RESEARCHING"})
    lead = await db.get_lead_by_id(lead_id)

    result = await orch_module.run_pain_point_analysis(lead)
    assert result["status"] == "SKIPPED"


async def test_pain_point_analysis_persists_pain_points_and_opportunities(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Researched Co", "website": "https://researchedco.io"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE", "industry": "Dental Care"})
    lead = await db.get_lead_by_id(lead_id)

    stub_result = AgentResult(
        status="ok",
        data={
            "pain_points": [{
                "title": "No visible booking", "confidence": 0.9,
                "severity": "high", "classification": "observed",
            }],
            "business_opportunities": [{
                "_pain_point_index": 0, "area": "Appointment Scheduling",
                "title": "Make appointment scheduling easier", "confidence": 0.9,
                "classification": "observed",
            }],
        },
        evidence=[EvidenceItem("pain_point:No visible booking", "heuristic", "https://researchedco.io", "cta=[]")],
        confidence=0.9,
    )
    monkeypatch.setattr(orch_module, "_pain_point_agent", _StubAgentPainPoint(stub_result))

    result = await orch_module.run_pain_point_analysis(lead)
    assert result["status"] == "DONE"
    assert result["pain_points_found"] == 1
    assert result["opportunities_found"] == 1

    pain_points = await db.get_pain_points(profile_id)
    assert len(pain_points) == 1
    assert pain_points[0]["title"] == "No visible booking"

    opportunities = await db.get_business_opportunities(profile_id)
    assert len(opportunities) == 1
    assert opportunities[0]["pain_point_id"] == pain_points[0]["id"]

    evidence = await db.get_research_evidence(profile_id)
    assert any(e["agent_name"] == "pain_point" for e in evidence)


async def test_pain_point_analysis_rerun_does_not_duplicate(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Rerun Co", "website": "https://rerunco.io"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    lead = await db.get_lead_by_id(lead_id)

    stub_result = AgentResult(
        status="ok",
        data={
            "pain_points": [{"title": "No SSL", "confidence": 0.8, "classification": "observed"}],
            "business_opportunities": [],
        },
        evidence=[], confidence=0.8,
    )
    monkeypatch.setattr(orch_module, "_pain_point_agent", _StubAgentPainPoint(stub_result))

    await orch_module.run_pain_point_analysis(lead)
    await orch_module.run_pain_point_analysis(lead)

    pain_points = await db.get_pain_points(profile_id)
    assert len(pain_points) == 1


async def test_pain_point_analysis_returns_failed_on_agent_failure(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Failing Co", "website": "https://failingco.io"})
    await db.upsert_company_profile(lead_id, {"status": "DONE"})
    lead = await db.get_lead_by_id(lead_id)

    monkeypatch.setattr(orch_module, "_pain_point_agent", _CrashingPainPointAgent())

    result = await orch_module.run_pain_point_analysis(lead)
    assert result["status"] == "FAILED"


async def test_pain_point_analysis_handles_none_lead(clean_db):
    result = await orch_module.run_pain_point_analysis(None)
    assert result["status"] == "FAILED"
    assert result["lead_id"] is None


async def test_lead_missing_id_returns_failed_instead_of_raising():
    # lead_id extraction (lead["id"]) must happen inside the try block — a malformed
    # lead argument must never escape run_research_pipeline as an uncaught exception,
    # since this is wired into live campaign traffic.
    result = await orch_module.run_research_pipeline({"business_name": "No ID Co"})
    assert result["status"] == "FAILED"
    assert result["lead_id"] is None
    assert "error" in result


async def test_none_lead_returns_failed_instead_of_raising():
    result = await orch_module.run_research_pipeline(None)
    assert result["status"] == "FAILED"
    assert result["lead_id"] is None
    assert "error" in result


# ─────────────────────────────────────────────────────────────────────────────
# run_opportunity_analysis (Phase 2) — separate entry point, requires pain
# points to already exist. Does not touch run_research_pipeline/
# run_pain_point_analysis tests or behavior above.
# ─────────────────────────────────────────────────────────────────────────────

async def test_opportunity_analysis_skipped_when_no_profile(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "No Profile Co"})
    lead = await db.get_lead_by_id(lead_id)

    result = await orch_module.run_opportunity_analysis(lead)
    assert result["status"] == "SKIPPED"


async def test_opportunity_analysis_skipped_when_no_pain_points(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "No Pain Points Co"})
    await db.upsert_company_profile(lead_id, {"status": "DONE"})
    lead = await db.get_lead_by_id(lead_id)

    result = await orch_module.run_opportunity_analysis(lead)
    assert result["status"] == "SKIPPED"
    assert "pain points" in result["reason"]


async def test_opportunity_analysis_persists_opportunities_and_solutions(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Researched Co", "website": "https://researchedco.io", "niche": "dentist"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE", "industry": "Dental Care"})
    await db.replace_pain_points(profile_id, [
        {"title": "No visible online appointment/booking system", "confidence": 0.9,
         "severity": "high", "classification": "observed"},
    ])
    lead = await db.get_lead_by_id(lead_id)

    stub_result = AgentResult(
        status="ok",
        data={"opportunities": [{
            "opportunity": "Appointment automation",
            "why_it_matters": "Manual customer communication currently required for scheduling.",
            "business_ease": "Make appointment scheduling easier",
            "area": "Appointment Scheduling",
            "priority": "HIGH",
            "confidence": 0.9,
            "classification": "observed",
            "pain_point_id": None,
            "_source_pain_points": [],
        }]},
        evidence=[EvidenceItem("opportunity:Appointment automation", "heuristic", None, "snippet")],
        confidence=0.9,
    )
    monkeypatch.setattr(orch_module, "_opportunity_agent", _StubAgentOpportunity(stub_result))

    result = await orch_module.run_opportunity_analysis(lead)
    assert result["status"] == "DONE"
    assert result["opportunities_found"] == 1

    opportunities = await db.get_business_opportunities(profile_id)
    assert len(opportunities) == 1
    assert opportunities[0]["title"] == "Appointment automation"
    assert opportunities[0]["business_ease"] == "Make appointment scheduling easier"
    assert opportunities[0]["priority"] == "HIGH"

    # Solution matching ran against the persisted opportunity's signal text —
    # "manual customer communication" is a WhatsApp Automation recommend_when keyword.
    solutions = await db.get_solution_recommendations(profile_id)
    assert len(solutions) == 1
    assert solutions[0]["service_name"] == "WhatsApp Automation"
    assert solutions[0]["business_opportunity_id"] == opportunities[0]["id"]

    # score_opportunity_fit ran too — intelligence fields populated without touching final_score.
    score = await db.get_score(lead_id)
    assert score["intelligence_score"] > 0


async def test_opportunity_analysis_no_solution_match_still_persists_opportunity(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Vague Co", "niche": "unknown"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [{"title": "X", "confidence": 0.5}])
    lead = await db.get_lead_by_id(lead_id)

    stub_result = AgentResult(
        status="ok",
        data={"opportunities": [{
            "opportunity": "", "why_it_matters": "", "business_ease": "",
            "area": "Customer Communication", "priority": "LOW", "confidence": 0.3,
            "classification": "inferred", "pain_point_id": None, "_source_pain_points": [],
        }]},
        evidence=[], confidence=0.3,
    )
    monkeypatch.setattr(orch_module, "_opportunity_agent", _StubAgentOpportunity(stub_result))

    result = await orch_module.run_opportunity_analysis(lead)
    assert result["status"] == "DONE"
    assert result["solutions_recommended"] == 0

    opportunities = await db.get_business_opportunities(profile_id)
    assert len(opportunities) == 1  # opportunity persisted even with no confident solution match
    assert await db.get_solution_recommendations(profile_id) == []


async def test_opportunity_analysis_rerun_does_not_duplicate(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Rerun Co", "niche": "dentist"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [{"title": "No SSL", "confidence": 0.8}])
    lead = await db.get_lead_by_id(lead_id)

    stub_result = AgentResult(
        status="ok",
        data={"opportunities": [{
            "opportunity": "Security fix", "why_it_matters": "website is not served over https",
            "business_ease": "Make security easier", "area": "Customer Communication",
            "priority": "MEDIUM", "confidence": 0.8, "classification": "observed",
            "pain_point_id": None, "_source_pain_points": [],
        }]},
        evidence=[], confidence=0.8,
    )
    monkeypatch.setattr(orch_module, "_opportunity_agent", _StubAgentOpportunity(stub_result))

    await orch_module.run_opportunity_analysis(lead)
    await orch_module.run_opportunity_analysis(lead)

    assert len(await db.get_business_opportunities(profile_id)) == 1
    assert len(await db.get_solution_recommendations(profile_id)) <= 1


async def test_opportunity_analysis_returns_failed_on_agent_failure(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Failing Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [{"title": "X", "confidence": 0.5}])
    lead = await db.get_lead_by_id(lead_id)

    monkeypatch.setattr(orch_module, "_opportunity_agent", _CrashingOpportunityAgent())

    result = await orch_module.run_opportunity_analysis(lead)
    assert result["status"] == "FAILED"


async def test_opportunity_analysis_handles_none_lead(clean_db):
    result = await orch_module.run_opportunity_analysis(None)
    assert result["status"] == "FAILED"
    assert result["lead_id"] is None


# ─────────────────────────────────────────────────────────────────────────────
# run_marketing_agent (Phase 3) — separate entry point, requires pain points
# to already exist and refuses to run for opted-out (SKIPPED) leads. Does not
# touch any test/behavior above.
# ─────────────────────────────────────────────────────────────────────────────

async def test_marketing_agent_skipped_when_no_profile(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "No Profile Co"})
    lead = await db.get_lead_by_id(lead_id)

    result = await orch_module.run_marketing_agent(lead)
    assert result["status"] == "SKIPPED"


async def test_marketing_agent_skipped_when_no_pain_points(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "No Pain Points Co"})
    await db.upsert_company_profile(lead_id, {"status": "DONE"})
    lead = await db.get_lead_by_id(lead_id)

    result = await orch_module.run_marketing_agent(lead)
    assert result["status"] == "SKIPPED"
    assert "pain points" in result["reason"]


async def test_marketing_agent_skipped_for_opted_out_lead(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Opted Out Co", "status": "SKIPPED"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [{"title": "X", "confidence": 0.5}])
    lead = await db.get_lead_by_id(lead_id)

    result = await orch_module.run_marketing_agent(lead)
    assert result["status"] == "SKIPPED"
    assert "opted out" in result["reason"]


async def test_marketing_agent_persists_generated_messages(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Researched Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [{"title": "No booking", "confidence": 0.8}])
    lead = await db.get_lead_by_id(lead_id)

    stub_result = AgentResult(
        status="ok",
        data={"messages": [
            {"channel": "EMAIL", "variant": "PRIMARY", "message": "email body",
             "subject": "Quick thought", "pain_point": "No booking", "confidence": 0.8},
            {"channel": "WHATSAPP", "variant": "PRIMARY", "message": "wa body",
             "pain_point": "No booking", "confidence": 0.8},
        ]},
        evidence=[EvidenceItem("marketing:PRIMARY", "heuristic", None, "snippet")],
        confidence=0.8,
    )
    monkeypatch.setattr(orch_module, "_marketing_agent", _StubAgentMarketing(stub_result))

    result = await orch_module.run_marketing_agent(lead)
    assert result["status"] == "DONE"
    assert result["messages_generated"] == 2

    messages = await db.get_generated_messages(lead_id)
    assert len(messages) == 2
    assert all(m["company_profile_id"] == profile_id for m in messages)

    evidence = await db.get_research_evidence(profile_id)
    assert any(e["agent_name"] == "marketing" for e in evidence)


async def test_marketing_agent_rerun_does_not_duplicate(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Rerun Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [{"title": "X", "confidence": 0.7}])
    lead = await db.get_lead_by_id(lead_id)

    stub_result = AgentResult(
        status="ok",
        data={"messages": [{"channel": "EMAIL", "variant": "PRIMARY", "message": "body", "confidence": 0.7}]},
        evidence=[], confidence=0.7,
    )
    monkeypatch.setattr(orch_module, "_marketing_agent", _StubAgentMarketing(stub_result))

    await orch_module.run_marketing_agent(lead)
    await orch_module.run_marketing_agent(lead)

    assert len(await db.get_generated_messages(lead_id)) == 1


async def test_marketing_agent_returns_failed_on_agent_crash(clean_db, monkeypatch):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Failing Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [{"title": "X", "confidence": 0.5}])
    lead = await db.get_lead_by_id(lead_id)

    monkeypatch.setattr(orch_module, "_marketing_agent", _CrashingMarketingAgent())

    result = await orch_module.run_marketing_agent(lead)
    assert result["status"] == "FAILED"


async def test_marketing_agent_handles_none_lead(clean_db):
    result = await orch_module.run_marketing_agent(None)
    assert result["status"] == "FAILED"
    assert result["lead_id"] is None
