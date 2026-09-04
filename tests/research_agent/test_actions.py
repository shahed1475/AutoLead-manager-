import pytest

from backend.research_agent.actions import ActionValidationError, validate_action
from backend.research_agent.models import AgentAction

VALID_EXAMPLES = [
    AgentAction(action="google_search", params={"query": "dental clinics Abbeville"}),
    AgentAction(action="open_url", params={"url": "https://example-dental.test"}),
    AgentAction(action="extract_page_text", params={}),
    AgentAction(action="find_links", params={}),
    AgentAction(action="find_links", params={"keyword": "contact"}),
    AgentAction(action="click", params={"text": "Contact Us"}),
    AgentAction(action="click", params={"selector": "#contact-link"}),
    AgentAction(action="go_back", params={}),
    AgentAction(action="open_new_tab", params={"url": "https://example-dental.test"}),
    AgentAction(action="save_evidence", params={"field_name": "business_phone", "value": "5551234567", "confidence": 0.8, "status": "FOUND"}),
    AgentAction(action="finish_research", params={"reason": "done"}),
]


@pytest.mark.parametrize("action", VALID_EXAMPLES, ids=[a.action for a in VALID_EXAMPLES])
def test_valid_actions_accepted(action):
    validate_action(action)  # must not raise


def test_unknown_action_rejected():
    with pytest.raises(ActionValidationError, match="Unknown action"):
        validate_action(AgentAction(action="delete_all_files"))


def test_dangerous_uncontrolled_action_rejected():
    """Any action name outside the fixed VALID_ACTIONS set must fail closed
    — there is no escape hatch for arbitrary browser/system control."""
    for dangerous in ("execute_javascript", "download_file", "run_shell_command", "eval", "fetch_raw_html_and_exec"):
        with pytest.raises(ActionValidationError):
            validate_action(AgentAction(action=dangerous, params={"code": "whatever"}))


def test_google_search_requires_query():
    with pytest.raises(ActionValidationError, match="query"):
        validate_action(AgentAction(action="google_search", params={}))
    with pytest.raises(ActionValidationError):
        validate_action(AgentAction(action="google_search", params={"query": "   "}))


def test_open_url_requires_http_scheme():
    with pytest.raises(ActionValidationError, match="url"):
        validate_action(AgentAction(action="open_url", params={"url": "javascript:alert(1)"}))
    with pytest.raises(ActionValidationError):
        validate_action(AgentAction(action="open_url", params={"url": "file:///etc/passwd"}))
    with pytest.raises(ActionValidationError):
        validate_action(AgentAction(action="open_url", params={}))


def test_open_new_tab_requires_http_scheme():
    with pytest.raises(ActionValidationError):
        validate_action(AgentAction(action="open_new_tab", params={"url": "ftp://example.test"}))


def test_click_requires_text_or_selector():
    with pytest.raises(ActionValidationError, match="text.*selector|selector.*text"):
        validate_action(AgentAction(action="click", params={}))


def test_scroll_and_screenshot_are_retired_actions():
    """Scrolling and screenshot capture are now owned entirely by
    PageReader (reader.py) — the LLM can no longer request them as
    standalone actions."""
    for retired in ("scroll", "screenshot"):
        with pytest.raises(ActionValidationError, match="Unknown action"):
            validate_action(AgentAction(action=retired, params={"direction": "down"}))


def test_save_evidence_requires_field_name_and_value():
    with pytest.raises(ActionValidationError, match="field_name"):
        validate_action(AgentAction(action="save_evidence", params={"value": "x"}))
    with pytest.raises(ActionValidationError, match="value"):
        validate_action(AgentAction(action="save_evidence", params={"field_name": "business_phone", "value": ""}))
