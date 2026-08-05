from backend.intelligence.techstack import detect_tech_stack


def test_detects_wordpress():
    html = '<html><head><link rel="stylesheet" href="/wp-content/themes/x/style.css"></head></html>'
    assert "WordPress" in detect_tech_stack(html)


def test_detects_shopify():
    html = '<script src="https://cdn.shopify.com/s/files/1/foo.js"></script>'
    assert "Shopify" in detect_tech_stack(html)


def test_detects_multiple_signatures():
    html = '<div id="wp-content"></div><script>gtag("config")</script>'
    stack = detect_tech_stack(html)
    assert "WordPress" in stack
    assert "Google Analytics" in stack


def test_empty_or_none_html_returns_empty_list():
    assert detect_tech_stack("") == []
    assert detect_tech_stack(None) == []


def test_no_matches_returns_empty_list():
    assert detect_tech_stack("<html><body>Plain site, no known signatures</body></html>") == []
