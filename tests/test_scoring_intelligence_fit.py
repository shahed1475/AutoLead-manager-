import pytest

from backend.scoring.lead_scorer import score_lead, score_opportunity_fit

pytestmark = pytest.mark.asyncio


async def test_no_profile_returns_zero_without_erroring(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "No Profile Co"})
    result = await score_opportunity_fit(lead_id)
    assert result["intelligence_score"] == 0.0
    assert result["intelligence_category"] == "COLD"


async def test_no_pain_points_or_opportunities_scores_zero(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Empty Profile Co"})
    await db.upsert_company_profile(lead_id, {"status": "DONE"})
    result = await score_opportunity_fit(lead_id)
    assert result["intelligence_score"] == 0.0


async def test_high_severity_observed_pain_point_scores_higher_than_low(clean_db):
    db = clean_db

    lead_a = await db.create_lead({"business_name": "High Severity Co"})
    profile_a = await db.upsert_company_profile(lead_a, {"status": "DONE"})
    await db.replace_pain_points(profile_a, [
        {"title": "No contact method", "confidence": 0.9, "severity": "high", "classification": "observed"},
    ])

    lead_b = await db.create_lead({"business_name": "Low Severity Co"})
    profile_b = await db.upsert_company_profile(lead_b, {"status": "DONE"})
    await db.replace_pain_points(profile_b, [
        {"title": "Minor issue", "confidence": 0.9, "severity": "low", "classification": "observed"},
    ])

    result_a = await score_opportunity_fit(lead_a)
    result_b = await score_opportunity_fit(lead_b)
    assert result_a["intelligence_score"] > result_b["intelligence_score"]


async def test_solution_match_increases_score(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Matched Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [
        {"title": "X", "confidence": 0.7, "severity": "medium", "classification": "observed"},
    ])

    result_before = await score_opportunity_fit(lead_id)

    await db.replace_solution_recommendations(profile_id, [
        {"service_name": "WhatsApp Automation", "confidence": 0.9, "evidence_ids": []},
    ])
    result_after = await score_opportunity_fit(lead_id)

    assert result_after["intelligence_score"] > result_before["intelligence_score"]
    assert result_after["solution_fit_score"] > 0


async def test_intelligence_score_capped_at_100(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Maxed Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [
        {"title": "A", "confidence": 1.0, "severity": "high", "classification": "observed"},
        {"title": "B", "confidence": 1.0, "severity": "high", "classification": "observed"},
    ])
    await db.replace_business_opportunities(profile_id, [
        {"area": "X", "title": "Y", "priority": "HIGH", "confidence": 1.0},
    ])
    await db.replace_solution_recommendations(profile_id, [
        {"service_name": "WhatsApp Automation", "confidence": 1.0, "evidence_ids": []},
    ])

    result = await score_opportunity_fit(lead_id)
    assert result["intelligence_score"] <= 100.0


async def test_intelligence_category_uses_same_thresholds_as_hot_warm_cold(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Hot Intel Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [
        {"title": "A", "confidence": 1.0, "severity": "high", "classification": "observed"},
        {"title": "B", "confidence": 1.0, "severity": "high", "classification": "observed"},
    ])
    await db.replace_business_opportunities(profile_id, [
        {"area": "X", "title": "Y", "priority": "HIGH", "confidence": 1.0},
    ])
    await db.replace_solution_recommendations(profile_id, [
        {"service_name": "WhatsApp Automation", "confidence": 1.0, "evidence_ids": []},
    ])

    result = await score_opportunity_fit(lead_id)
    assert result["intelligence_score"] >= 70
    assert result["intelligence_category"] == "HOT"


async def test_persists_to_scores_table(clean_db):
    db = clean_db
    lead_id = await db.create_lead({"business_name": "Persisted Co"})
    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [
        {"title": "X", "confidence": 0.6, "severity": "medium", "classification": "observed"},
    ])

    await score_opportunity_fit(lead_id)
    stored = await db.get_score(lead_id)
    assert stored is not None
    assert stored["intelligence_score"] > 0


async def test_does_not_touch_final_score_or_category_from_score_lead(clean_db):
    """The core, existing HOT/WARM/COLD score_lead() logic must be completely
    unaffected by running score_opportunity_fit — this is the Phase 2 brief's
    explicit 'do not destroy the existing scoring logic' requirement."""
    db = clean_db
    lead_id = await db.create_lead({
        "business_name": "Untouched Score Co", "website": "https://untouchedscoreco.io",
        "rating": 4.8, "reviews_count": 100, "niche": "dental clinic",
    })
    lead = await db.get_lead_by_id(lead_id)

    before = await score_lead(lead)
    assert before["final_score"] > 0
    assert before["category"] in ("HOT", "WARM", "COLD")

    profile_id = await db.upsert_company_profile(lead_id, {"status": "DONE"})
    await db.replace_pain_points(profile_id, [
        {"title": "X", "confidence": 0.9, "severity": "high", "classification": "observed"},
    ])
    await score_opportunity_fit(lead_id)

    after = await db.get_score(lead_id)
    assert after["final_score"] == before["final_score"]
    assert after["category"] == before["category"]

    lead_after = await db.get_lead_by_id(lead_id)
    assert lead_after["score"] == int(before["final_score"])
    assert lead_after["score_label"] == before["category"]
