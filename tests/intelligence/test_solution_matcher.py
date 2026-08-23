from backend.intelligence.solution_matcher import match_solution


def _opportunity(**overrides):
    base = {
        "opportunity": "Appointment automation",
        "business_ease": "Make appointment scheduling easier",
        "why_it_matters": "Manual customer communication currently required for every booking",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


def _pain_point(**overrides):
    base = {"title": "No easy way for customers to reach the business online", "description": ""}
    base.update(overrides)
    return base


def test_matches_whatsapp_automation_for_manual_communication_signal():
    opp = _opportunity()
    pain_points = [_pain_point()]
    result = match_solution(opp, pain_points, {"industry": "Dental Care"}, [])

    assert result is not None
    assert result["service"] == "WhatsApp Automation"
    assert "detected" in result["reason"].lower()
    assert 0.0 <= result["confidence"] <= 1.0


def test_confidence_never_exceeds_opportunity_confidence():
    opp = _opportunity(confidence=0.3)
    result = match_solution(opp, [_pain_point()], {"industry": "Dental Care"}, [])
    assert result is not None
    assert result["confidence"] <= 0.3


def test_returns_none_when_no_signal_matches_any_service():
    opp = {"opportunity": "", "business_ease": "", "why_it_matters": "", "confidence": 0.5}
    result = match_solution(opp, [], {}, [])
    assert result is None


def test_only_one_service_is_ever_returned():
    opp = _opportunity(
        why_it_matters="manual customer communication, no crm, manual lead intake, repetitive question"
    )
    result = match_solution(opp, [_pain_point()], {"industry": "Dental Care"}, [])
    assert result is not None
    assert isinstance(result["service"], str)  # single string, not a list


def test_do_not_recommend_condition_excludes_service():
    # "no visible online appointment/booking system" would normally match
    # WhatsApp Automation and E-commerce, but "already has crm" should never
    # exclude WhatsApp here — instead verify a genuine exclusion case:
    # E-commerce is excluded for a pure service business with no product to sell.
    opp = _opportunity(
        opportunity="Online ordering",
        business_ease="online store sell online",
        why_it_matters="ecommerce platform already exists",
    )
    result = match_solution(opp, [_pain_point()], {"industry": "restaurant"}, [])
    # "ecommerce platform" is a WhatsApp Automation do_not_recommend_when condition
    assert result is None or result["service"] != "WhatsApp Automation"


def test_evidence_ids_traced_to_matching_pain_point_evidence():
    pain_points = [_pain_point(title="No SSL")]
    evidence = [
        {"id": 101, "field_name": "pain_point:No SSL", "snippet": "has_ssl=False"},
        {"id": 102, "field_name": "pain_point:Unrelated pain point", "snippet": "x"},
        {"id": 103, "field_name": "industry", "snippet": "y"},
    ]
    opp = _opportunity(why_it_matters="website is not served over https")
    result = match_solution(opp, pain_points, {"industry": "Dental Care"}, evidence)

    assert result is not None
    assert result["evidence_ids"] == [101]


def test_evidence_ids_empty_when_no_matching_evidence_rows():
    opp = _opportunity()
    result = match_solution(opp, [_pain_point()], {"industry": "Dental Care"}, [])
    assert result["evidence_ids"] == []
