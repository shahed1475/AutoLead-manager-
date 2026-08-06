from backend.outreach_domain import build_domain_context, get_domain_profile


def test_matches_food_service_including_typos():
    # "resturent" and "bekari" are the actual typo'd values present in production data
    assert get_domain_profile("resturent").label == "restaurant / cafe / bakery"
    assert get_domain_profile("bekari").label == "restaurant / cafe / bakery"
    assert get_domain_profile("Bakery").label == "restaurant / cafe / bakery"


def test_matches_real_estate():
    assert get_domain_profile("Real Estate Agencies").label == "real estate agency / agent"
    assert get_domain_profile("realty").label == "real estate agency / agent"


def test_unknown_niche_falls_back_to_generic():
    assert get_domain_profile("").label == "local business"
    assert get_domain_profile(None).label == "local business"
    assert get_domain_profile("some totally unrelated made-up niche xyz").label == "local business"


def test_generic_profile_explicitly_warns_against_inventing_detail():
    profile = get_domain_profile("")
    assert "do not invent detail" in profile.tone_notes.lower()


def test_build_domain_context_includes_pain_points_and_value_angles():
    ctx = build_domain_context("resturent")
    assert "pain point" in ctx.lower()
    assert "value angle" in ctx.lower()
    assert "inconsistent online ordering" in ctx or "repeat-visit" in ctx
