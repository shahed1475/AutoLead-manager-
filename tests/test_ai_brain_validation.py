"""
Tests for ai_brain.py's content-quality gate — the defense added after real
production messages were found containing unfilled bracket placeholders, raw
HTML tags, hashtags, and near-identical "follow-up" messages (see git history /
project memory for the specific examples that motivated this).
"""
from backend.ai_brain import (
    _messages_too_similar,
    _validate_message_content,
    _validate_message_dict,
)


# ── _validate_message_content ──────────────────────────────────────────────────

def test_clean_message_has_no_violations():
    text = "Hi there, I noticed your bakery has great reviews. Would you be open to a quick chat?"
    assert _validate_message_content(text) == []


def test_detects_bracket_placeholder():
    text = "Hi [Business Name], we'd love to help you grow."
    violations = _validate_message_content(text)
    assert any("bracket placeholder" in v for v in violations)


def test_detects_bracket_placeholder_real_production_example():
    # Exact shape found in production: a manager-name placeholder never filled in.
    text = "Hi [Damand Bakery & Cafe's Manager Name], we noticed you've been focusing on..."
    violations = _validate_message_content(text)
    assert any("bracket placeholder" in v for v in violations)


def test_detects_html_tag():
    text = "Check out <a href='http://example.com'>our website</a> for more."
    violations = _validate_message_content(text)
    assert any("HTML" in v for v in violations)


def test_detects_hashtag():
    text = "Loving the bakery vibes! #BakeryTech Revolution!"
    violations = _validate_message_content(text)
    assert any("hashtag" in v for v in violations)


def test_detects_excessive_emoji():
    text = "Hey there! 🎉🍪✨🌞🍰 Let's chat!"
    violations = _validate_message_content(text)
    assert any("emoji" in v for v in violations)


def test_single_emoji_is_allowed():
    text = "Hey there! 🎉 Would love to chat about your bakery."
    assert _validate_message_content(text) == []


def test_empty_or_non_string_input_has_no_violations():
    assert _validate_message_content("") == []
    assert _validate_message_content(None) == []


def test_multiple_violations_all_reported():
    text = "Hi [Name]! <b>Check this out</b> #growth 🎉🍪✨"
    violations = _validate_message_content(text)
    assert len(violations) >= 3


# ── _messages_too_similar ──────────────────────────────────────────────────────

def test_identical_messages_are_too_similar():
    a = "Hey there, it's been 3 days since we spoke. Any thoughts on our proposal?"
    assert _messages_too_similar(a, a) is True


def test_real_production_near_duplicate_example():
    # Exact near-duplicate pair found in production (follow_up_2 vs follow_up_3
    # for the same lead — should have been substantively different messages).
    a = ("Hey Damand Bakery & Cafe, I hope you're doing great. It's been a while since "
         "I last chatted with you about your bakery. What if I told you that our "
         "AI-powered system can help you automate and streamline all of your processes?")
    b = ("Hey Damand Bakery & Cafe, I hope you're doing great. It's been a while since "
         "I last chatted with you about your bakery. What if I told you that our "
         "AI-powered system can help you automate and streamline everything?")
    assert _messages_too_similar(a, b) is True


def test_genuinely_different_messages_are_not_similar():
    a = "Hi! Loved your bakery's reviews — would you be open to a quick chat about online ordering?"
    b = "Following up briefly: we helped a nearby cafe cut no-shows in half with automated reminders. Worth a look?"
    assert _messages_too_similar(a, b) is False


def test_empty_strings_are_never_similar():
    assert _messages_too_similar("", "") is False
    assert _messages_too_similar("hello", "") is False


# ── _validate_message_dict ─────────────────────────────────────────────────────

def test_validate_message_dict_flags_field_and_key_name():
    parsed = {"first_message": "Hi [Name]!", "follow_up_1": "Clean message here."}
    violations = _validate_message_dict(parsed)
    assert any(v.startswith("first_message:") for v in violations)
    assert not any(v.startswith("follow_up_1:") for v in violations)


def test_validate_message_dict_catches_near_duplicate_followups():
    text = "Hey there, checking in about your bakery's online presence and growth plans."
    parsed = {
        "first_message": "A totally different opening message about something else entirely.",
        "follow_up_1": text,
        "follow_up_2": text,
    }
    violations = _validate_message_dict(parsed, similarity_groups=[["follow_up_1", "follow_up_2"]])
    assert any("near-duplicate" in v for v in violations)


def test_validate_message_dict_clean_dict_has_no_violations():
    parsed = {
        "first_message": "Hi! Noticed your bakery has great reviews — open to a quick chat?",
        "follow_up_1":   "Following up briefly — happy to share how we've helped similar spots nearby.",
    }
    assert _validate_message_dict(parsed, similarity_groups=[["first_message", "follow_up_1"]]) == []
