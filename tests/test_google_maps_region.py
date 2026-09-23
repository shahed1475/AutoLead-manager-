"""Google Maps searches must target the requested country in English."""
from backend.scrapers import google_maps as gm


def test_region_code_resolution():
    assert gm._region_code("usa") == "us"
    assert gm._region_code("Dubai") == "ae"
    assert gm._region_code("Austin, TX", "United States") == "us"
    assert gm._region_code("Manchester, UK") == "gb"
    assert gm._region_code("Springfield") is None


def test_search_url_forces_english_and_region():
    assert gm._maps_search_url("dentist in Dubai", "ae") == \
        "https://www.google.com/maps/search/dentist%20in%20Dubai?hl=en&gl=ae"
    assert gm._maps_search_url("dentist", None).endswith("?hl=en")


def test_country_target_expands_to_cities():
    assert "New York, NY" in gm._country_cities("usa")
    assert gm._country_cities("Austin") == []


def test_foreign_phones_are_detected():
    # real rows from a 'usa' run made from a Bangladeshi IP
    assert gm._phone_outside_region("01717624021", "us")      # BD domestic format
    assert gm._phone_outside_region("+918980623275", "us")    # India
    assert gm._phone_outside_region("+971502563979", "us")    # UAE
    assert not gm._phone_outside_region("+17137428887", "us")
    assert not gm._phone_outside_region("(212) 555-0142", "us")
    assert not gm._phone_outside_region("+97142517887", "ae")
    assert not gm._phone_outside_region(None, "us")
    assert not gm._phone_outside_region("01717624021", None)
