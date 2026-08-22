import pytest

pytestmark = pytest.mark.asyncio


async def test_company_profile_upsert_and_get(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Acme Corp", "email": "a@acmecorp.io"})

    profile_id = await db.upsert_company_profile(lead_id, {
        "status": "RESEARCHING",
        "industry": "SaaS",
        "services": ["consulting", "support"],
    })
    assert profile_id

    profile = await db.get_company_profile(lead_id)
    assert profile["status"] == "RESEARCHING"
    assert profile["industry"] == "SaaS"
    assert profile["services"] == ["consulting", "support"]  # JSON round-trip

    profile_id2 = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    assert profile_id2 == profile_id
    updated = await db.get_company_profile(lead_id)
    assert updated["status"] == "DONE"
    assert updated["industry"] == "SaaS"  # untouched fields preserved


async def test_get_company_profile_returns_none_when_absent(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "No Profile Co"})
    assert await db.get_company_profile(lead_id) is None


async def test_research_evidence_roundtrip(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Beta LLC"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})

    await db.add_research_evidence(profile_id, [
        {"agent_name": "qualification", "field_name": "qualification_status",
         "source_type": "heuristic", "source_url": None, "snippet": "all checks passed"},
    ])
    evidence = await db.get_research_evidence(profile_id)
    assert len(evidence) == 1
    assert evidence[0]["agent_name"] == "qualification"
    assert evidence[0]["snippet"] == "all checks passed"


async def test_cascade_delete_removes_profile_and_evidence(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Gamma Inc"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.add_research_evidence(profile_id, [
        {"agent_name": "qualification", "field_name": "x", "source_type": "heuristic",
         "source_url": None, "snippet": None},
    ])

    deleted = await db.delete_lead(lead_id)
    assert deleted
    assert await db.get_company_profile(lead_id) is None


async def test_get_leads_without_company_profile(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Delta Co"})
    leads = await db.get_leads_without_company_profile(limit=10)
    assert any(l["id"] == lead_id for l in leads)

    await db.upsert_company_profile(lead_id, {"status": "PENDING"})
    leads = await db.get_leads_without_company_profile(limit=10)
    assert not any(l["id"] == lead_id for l in leads)


async def test_get_pending_company_profiles(clean_db):
    db = clean_db
    lead_id_1 = await db.create_lead({"business_name": "Pending Co"})
    lead_id_2 = await db.create_lead({"business_name": "Done Co"})
    await db.upsert_company_profile(lead_id_1, {"status": "PENDING"})
    await db.upsert_company_profile(lead_id_2, {"status": "DONE"})

    pending = await db.get_pending_company_profiles(limit=10)
    assert {p["lead_id"] for p in pending} == {lead_id_1}


async def test_restart_sweep_resets_stuck_statuses(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Epsilon Ltd"})
    await db.upsert_company_profile(lead_id, {"status": "RESEARCHING"})

    await db.init_db()  # simulates a process restart re-running migrations

    profile = await db.get_company_profile(lead_id)
    assert profile["status"] == "PENDING"


async def test_restart_sweep_resets_failed_profiles_for_retry(clean_db):
    """FAILED must not be a terminal state — a transient failure (LLM down,
    network blip) should become eligible for retry on the next restart, just
    like QUALIFYING/RESEARCHING."""
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Zeta Retry Co"})
    await db.upsert_company_profile(lead_id, {"status": "FAILED"})

    await db.init_db()  # simulates a process restart re-running migrations

    profile = await db.get_company_profile(lead_id)
    assert profile["status"] == "PENDING"

    pending = await db.get_pending_company_profiles(limit=10)
    assert any(p["lead_id"] == lead_id for p in pending)


# ─────────────────────────────────────────────────────────────────────────────
# Pain points & business opportunities
# ─────────────────────────────────────────────────────────────────────────────

async def test_replace_and_get_pain_points(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Eta Dental"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})

    ids = await db.replace_pain_points(profile_id, [
        {"title": "No visible online booking", "description": "desc",
         "evidence_snippet": "cta_buttons=[]", "source_url": "https://eta.co",
         "confidence": 0.9, "severity": "high", "classification": "observed",
         "operational_impact": "Staff handle scheduling by phone",
         "customer_impact": "Friction during booking"},
    ])
    assert len(ids) == 1

    points = await db.get_pain_points(profile_id)
    assert len(points) == 1
    assert points[0]["title"] == "No visible online booking"
    assert points[0]["classification"] == "observed"
    assert points[0]["confidence"] == 0.9


async def test_replace_pain_points_prevents_duplicates_on_rerun(clean_db):
    """Running analysis twice for the same profile must never accumulate rows —
    replace_pain_points always leaves exactly one row per current pain point."""
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Theta Clinic"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})

    item = {"title": "No SSL", "confidence": 0.8, "severity": "medium", "classification": "observed"}
    await db.replace_pain_points(profile_id, [item])
    await db.replace_pain_points(profile_id, [item])

    points = await db.get_pain_points(profile_id)
    assert len(points) == 1


async def test_replace_and_get_business_opportunities(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Iota Realty"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    [pain_point_id] = await db.replace_pain_points(profile_id, [
        {"title": "Manual lead intake", "confidence": 0.7, "severity": "medium", "classification": "inferred"},
    ])

    ids = await db.replace_business_opportunities(profile_id, [
        {"pain_point_id": pain_point_id, "area": "Lead Qualification",
         "title": "Automate initial lead qualification",
         "description": "Could reduce manual triage", "confidence": 0.7, "classification": "inferred"},
    ])
    assert len(ids) == 1

    opportunities = await db.get_business_opportunities(profile_id)
    assert len(opportunities) == 1
    assert opportunities[0]["area"] == "Lead Qualification"
    assert opportunities[0]["pain_point_id"] == pain_point_id


async def test_replace_business_opportunities_prevents_duplicates_on_rerun(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Kappa Agency"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})

    item = {"area": "Client Management", "title": "Centralize client updates", "confidence": 0.6}
    await db.replace_business_opportunities(profile_id, [item])
    await db.replace_business_opportunities(profile_id, [item])

    opportunities = await db.get_business_opportunities(profile_id)
    assert len(opportunities) == 1


async def test_cascade_delete_removes_pain_points_and_opportunities(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Lambda Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [
        {"title": "No contact form", "confidence": 0.8, "classification": "observed"},
    ])
    await db.replace_business_opportunities(profile_id, [
        {"area": "Customer Support", "title": "Add a contact form", "confidence": 0.8},
    ])

    assert await db.delete_lead(lead_id)
    assert await db.get_pain_points(profile_id) == []
    assert await db.get_business_opportunities(profile_id) == []
