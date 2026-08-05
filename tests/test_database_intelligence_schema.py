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
