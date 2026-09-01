from backend.research_agent import evidence as evidence_mod
from backend.research_agent.models import STATUS_FOUND, STATUS_NOT_FOUND, ResearchLead


def test_record_finding_sets_field_and_evidence():
    lead = ResearchLead(business_name="Acme Dental")
    evidence_mod.record_finding(
        lead, "business_phone", "5551234567", source_type="website",
        source_url="https://acme.test/contact", snippet="Call us: 555-123-4567",
        confidence=0.8, status=STATUS_FOUND,
    )
    assert lead.business_phone == "5551234567"
    assert len(lead.evidence) == 1
    e = lead.evidence[0]
    assert e.field_name == "business_phone"
    assert e.source_url == "https://acme.test/contact"
    assert e.snippet == "Call us: 555-123-4567"
    assert e.status == STATUS_FOUND


def test_first_higher_confidence_source_wins_but_second_still_recorded():
    lead = ResearchLead(business_name="Acme Dental")
    evidence_mod.record_finding(lead, "business_email", "info@acme.test", "website", confidence=0.9)
    evidence_mod.record_finding(lead, "business_email", "wrong@guess.test", "google_search", confidence=0.3)

    assert lead.business_email == "info@acme.test"  # not overwritten by the weaker source
    assert len(lead.evidence) == 2  # both recorded — corroboration/conflict is preserved, not discarded


def test_multiple_sources_for_different_fields_all_stored():
    lead = ResearchLead(business_name="Acme Dental")
    evidence_mod.record_finding(lead, "business_phone", "5551234567", "website", source_url="https://acme.test", confidence=0.8)
    evidence_mod.record_finding(lead, "business_email", "info@acme.test", "website", source_url="https://acme.test/contact", confidence=0.8)
    evidence_mod.record_finding(lead, "management_contact_name", "Emma Papp", "ai_extraction", source_url="https://acme.test/team", confidence=0.65)

    assert len(lead.evidence) == 3
    fields = {e.field_name for e in lead.evidence}
    assert fields == {"business_phone", "business_email", "management_contact_name"}


def test_extracted_field_is_traceable_to_its_evidence():
    lead = ResearchLead(business_name="Acme Dental")
    evidence_mod.record_finding(lead, "management_title", "Office Manager", "ai_extraction", source_url="https://acme.test/team", snippet="Emma Papp — Office Manager", confidence=0.65)

    matches = [e for e in lead.evidence if e.field_name == "management_title"]
    assert len(matches) == 1
    assert matches[0].snippet == "Emma Papp — Office Manager"
    assert matches[0].source_url == "https://acme.test/team"


def test_record_not_found_does_not_set_field():
    lead = ResearchLead(business_name="Acme Dental")
    evidence_mod.record_not_found(lead, "management_contact_name", reason="no team page found")
    assert lead.management_contact_name is None
    assert lead.evidence[0].status == STATUS_NOT_FOUND
    assert lead.evidence[0].field_name == "management_contact_name"


def test_email_status_companion_field_kept_in_sync():
    lead = ResearchLead(business_name="Acme Dental")
    evidence_mod.record_finding(lead, "business_email", "info@acme.test", "website", confidence=0.8, status=STATUS_FOUND)
    assert lead.business_email_status == STATUS_FOUND

    lead2 = ResearchLead(business_name="Beta Dental")
    evidence_mod.record_not_found(lead2, "business_email")
    assert lead2.business_email_status == "NOT_FOUND"


def test_confidence_is_clamped_to_0_1():
    from backend.research_agent.models import ResearchEvidence
    ev = ResearchEvidence(field_name="x", source_type="website", confidence=5.0)
    assert ev.confidence == 1.0
    ev2 = ResearchEvidence(field_name="x", source_type="website", confidence=-2.0)
    assert ev2.confidence == 0.0


def test_invalid_status_falls_back_to_unconfirmed():
    from backend.research_agent.models import STATUS_UNCONFIRMED, ResearchEvidence
    ev = ResearchEvidence(field_name="x", source_type="website", status="MADE_UP_STATUS")
    assert ev.status == STATUS_UNCONFIRMED
