from backend.research_agent import evidence as evidence_mod
from backend.research_agent import validation
from backend.research_agent.models import (
    RESEARCH_COMPLETE,
    RESEARCH_FAILED,
    RESEARCH_PARTIAL,
    STATUS_FOUND,
    STATUS_SECURE_WEB_FORM,
    ResearchEvidence,
    ResearchLead,
)


def test_missing_email_stays_null_and_not_found():
    lead = ResearchLead(business_name="Acme Dental", business_phone="555", business_website="https://acme.test")
    evidence_mod.record_not_found(lead, "business_email", reason="no email found on site")
    assert lead.business_email is None
    assert lead.business_email_status == "NOT_FOUND"


def test_secure_web_form_represented_correctly():
    lead = ResearchLead(business_name="Acme Dental")
    lead.business_email_status = STATUS_SECURE_WEB_FORM
    assert lead.business_email is None
    assert lead.business_email_status == STATUS_SECURE_WEB_FORM


def test_finalize_status_writes_factual_research_notes():
    lead = ResearchLead(business_name="Acme Dental", business_phone="555", business_website="https://acme.test")
    lead.actions_taken, lead.pages_visited, lead.searches_taken = 6, 2, 1
    evidence_mod.record_finding(lead, "business_phone", "555", source_type="website",
                                source_url="https://acme.test/contact", confidence=0.8, status=STATUS_FOUND)
    validation.finalize_status(lead)
    notes = lead.research_notes
    assert notes and "2 page(s) visited" in notes
    assert "business_phone" in notes                      # reports what was actually found
    assert "https://acme.test/contact" in notes           # cites the source
    assert "No named owner/manager was publicly listed" in notes
    # Never asserts a value it didn't find:
    assert "@" not in notes.split("Sources:")[0]          # no invented email before the source list


def test_missing_management_contact_never_fabricated():
    lead = ResearchLead(business_name="Acme Dental", business_phone="555", business_website="https://acme.test")
    finalized = validation.finalize_status(lead)
    assert finalized.management_contact_name is None
    assert finalized.management_title is None
    # Required fields present -> COMPLETE even with no management info (brief:
    # optional fields never block completion, and are never invented to fill the gap).
    assert finalized.research_status == RESEARCH_COMPLETE


def test_business_phone_not_presented_as_personal_management_phone():
    lead = ResearchLead(business_name="Acme Dental", management_contact_name="Emma Papp")
    lead.management_phone = "5551234567"  # simulate the inference agent.py performs
    lead.management_phone_type = "BUSINESS"
    assert validation.management_phone_type_is_safe(lead)

    bad_lead = ResearchLead(business_name="Acme Dental", management_contact_name="Emma Papp")
    bad_lead.management_phone = "5551234567"  # phone set, but no type marker
    assert not validation.management_phone_type_is_safe(bad_lead)


def test_low_confidence_reflected_in_final_score():
    complete_lead = ResearchLead(business_name="A", business_phone="1", business_website="https://a.test")
    validation.finalize_status(complete_lead)

    partial_lead = ResearchLead(business_name="B", business_phone="1")  # missing website
    validation.finalize_status(partial_lead)

    failed_lead = ResearchLead()  # nothing found at all
    validation.finalize_status(failed_lead)

    assert complete_lead.research_status == RESEARCH_COMPLETE
    assert partial_lead.research_status == RESEARCH_PARTIAL
    assert failed_lead.research_status == RESEARCH_FAILED
    assert complete_lead.confidence > partial_lead.confidence > failed_lead.confidence


def test_validate_no_fabrication_catches_unbacked_field():
    lead = ResearchLead(business_name="Acme Dental")
    lead.business_phone = "5551234567"  # set directly, bypassing record_finding — simulates a bug
    violations = validation.validate_no_fabrication(lead)
    assert any("business_phone" in v for v in violations)


def test_validate_no_fabrication_clean_when_evidence_backed():
    lead = ResearchLead(business_name="Acme Dental")
    evidence_mod.record_finding(lead, "business_phone", "5551234567", "website", confidence=0.8, status=STATUS_FOUND)
    violations = validation.validate_no_fabrication(lead)
    assert violations == []


def test_is_research_sufficient():
    incomplete = ResearchLead(business_name="Acme Dental")
    assert not validation.is_research_sufficient(incomplete)

    complete = ResearchLead(business_name="Acme Dental", business_phone="555", business_website="https://acme.test")
    assert validation.is_research_sufficient(complete)


def test_not_found_after_search_status_is_a_valid_field_status():
    from backend.research_agent.models import STATUS_NOT_FOUND_AFTER_SEARCH, VALID_FIELD_STATUSES
    assert STATUS_NOT_FOUND_AFTER_SEARCH == "NOT_FOUND_AFTER_SEARCH"
    assert STATUS_NOT_FOUND_AFTER_SEARCH in VALID_FIELD_STATUSES


def test_record_not_found_after_search_marks_the_field_explicitly():
    lead = ResearchLead(business_name="Acme Dental", business_phone="555", business_website="https://acme.test")
    evidence_mod.record_not_found(lead, "management_email", reason="checked 3 pages + 2 searches", after_search=True)
    # the value stays null and is never fabricated
    assert lead.management_email is None
    # the email status companion reflects that we actually searched
    assert lead.management_email_status == "NOT_FOUND_AFTER_SEARCH"
    ev = [e for e in lead.evidence if e.field_name == "management_email"]
    assert len(ev) == 1
    assert ev[0].status == "NOT_FOUND_AFTER_SEARCH"
    assert "3 pages" in (ev[0].snippet or "")


def test_record_not_found_without_after_search_keeps_plain_not_found():
    lead = ResearchLead(business_name="Acme Dental")
    evidence_mod.record_not_found(lead, "business_email", reason="no email on site")
    assert lead.business_email_status == "NOT_FOUND"
