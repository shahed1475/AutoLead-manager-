from backend.intelligence import intelligence_enabled


def test_disabled_by_default():
    assert intelligence_enabled({}) is False


def test_enabled_when_true_string():
    assert intelligence_enabled({"sales_intelligence_enabled": "true"}) is True


def test_enabled_case_insensitive():
    assert intelligence_enabled({"sales_intelligence_enabled": "TRUE"}) is True


def test_disabled_when_false_string():
    assert intelligence_enabled({"sales_intelligence_enabled": "false"}) is False
