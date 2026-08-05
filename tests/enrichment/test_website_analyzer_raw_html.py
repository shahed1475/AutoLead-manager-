import pytest
import respx
import httpx

from backend.enrichment.website_analyzer import analyze_website

pytestmark = pytest.mark.asyncio


async def test_analyze_website_includes_raw_html():
    html = "<html><body><h1>Hello</h1><div class='wp-content'>x</div></body></html>"
    with respx.mock:
        respx.get("https://example-test-site.co").mock(
            return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"})
        )
        result = await analyze_website("https://example-test-site.co")

    assert "raw_html" in result
    assert "wp-content" in result["raw_html"]
