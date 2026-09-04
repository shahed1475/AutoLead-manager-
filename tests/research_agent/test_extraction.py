from backend.research_agent import extraction

# "Google AI style" realistic mocked page text, matching the brief's behavioral
# examples: a dental clinic team page with a named office manager.
TEAM_PAGE_TEXT = """
Acme Family Dental
123 Main St, Abbeville, LA

Contact us at info@acmefamilydental.test or call (337) 555-0142.

Our Team
Dr. John Smith — Lead Dentist
Emma Papp — Office Manager, has been with the practice for 10 years.

About Us | Services | Contact | Team
"""

CONTACT_FORM_ONLY_TEXT = """
Get in touch with our team.

Contact Us
Name: ____
Email: ____
Message: ____

We look forward to hearing from you.
"""


def test_extract_emails_finds_valid_email():
    emails = extraction.extract_emails(TEAM_PAGE_TEXT)
    assert "info@acmefamilydental.test" in emails


def test_extract_emails_returns_empty_for_no_email():
    assert extraction.extract_emails("No contact info here at all.") == []


def test_extract_emails_filters_invalid_format():
    assert extraction.extract_emails("reach us at not-an-email@@bad") == []


def test_extract_phones_finds_valid_phone():
    phones = extraction.extract_phones(TEAM_PAGE_TEXT)
    assert len(phones) >= 1


def test_extract_phones_returns_empty_for_no_phone():
    assert extraction.extract_phones("No phone number mentioned anywhere.") == []


def test_find_role_sentences_surfaces_management_lines():
    sentences = extraction.find_role_sentences(TEAM_PAGE_TEXT)
    joined = " ".join(sentences)
    assert "Office Manager" in joined or "Lead Dentist" in joined


def test_find_role_sentences_empty_for_no_role_mentions():
    assert extraction.find_role_sentences("Welcome to our homepage. We sell widgets.") == []


def test_is_relevant_nav_link():
    assert extraction.is_relevant_nav_link("Meet the Team")
    assert extraction.is_relevant_nav_link("Contact")
    assert not extraction.is_relevant_nav_link("Shop Now")


def test_has_secure_contact_form_true_for_form_page():
    assert extraction.has_secure_contact_form(CONTACT_FORM_ONLY_TEXT, links=[{"text": "Contact Us", "href": "https://x.test/contact"}])


def test_has_secure_contact_form_false_for_unrelated_page():
    assert not extraction.has_secure_contact_form("Our menu includes pizza and pasta.", links=[])


def test_find_role_sentences_recognises_dental_leadership_titles():
    text = "Dr. Amy Lin is the Principal Dentist and Practice Owner. Sara Cole, Clinic Manager, runs the front office."
    sentences = extraction.find_role_sentences(text)
    joined = " ".join(sentences).lower()
    assert "principal dentist" in joined or "practice owner" in joined
    assert "clinic manager" in joined


def test_emails_from_contact_links_reads_mailto_hrefs():
    links = [
        {"text": "Email us", "href": "mailto:office@abbevilledental.test?subject=Hi"},
        {"text": "Home", "href": "https://abbevilledental.test/"},
    ]
    assert extraction.emails_from_contact_links(links) == ["office@abbevilledental.test"]


def test_phones_from_contact_links_reads_tel_hrefs():
    links = [{"text": "Call", "href": "tel:+1-337-893-2614"}, {"text": "x", "href": "https://x.test"}]
    assert extraction.phones_from_contact_links(links) == ["+13378932614"]


def test_contact_links_helpers_ignore_junk():
    assert extraction.emails_from_contact_links([{"text": "x", "href": "mailto:not@@bad"}]) == []
    assert extraction.phones_from_contact_links([{"text": "x", "href": "tel:12"}]) == []


def test_link_relevance_people_pages_score_highest():
    assert extraction.link_relevance("https://x.test/team", "Meet Our Team") == 3
    assert extraction.link_relevance("https://x.test/about", "About") == 3
    assert extraction.link_relevance("https://x.test/providers", "Our Providers") == 3
    assert extraction.link_relevance("https://x.test/contact", "Contact Us") == 3


def test_link_relevance_offering_pages_score_medium():
    assert extraction.link_relevance("https://x.test/services", "Services") == 2
    assert extraction.link_relevance("https://x.test/book-appointment", "Book Now") == 2
    assert extraction.link_relevance("https://x.test/pricing", "Pricing") == 2


def test_link_relevance_peripheral_pages_score_low():
    assert extraction.link_relevance("https://x.test/careers", "Careers") == 1
    assert extraction.link_relevance("https://x.test/blog/post-1", "Blog") == 1


def test_link_relevance_irrelevant_pages_score_zero():
    assert extraction.link_relevance("https://x.test/shop", "Shop Now") == 0
    assert extraction.link_relevance("https://x.test/cart", "Cart") == 0


def test_link_relevance_checks_href_even_with_no_anchor_text():
    assert extraction.link_relevance("https://x.test/our-team/", "") == 3


def test_is_relevant_nav_link_still_works_as_a_shim():
    assert extraction.is_relevant_nav_link("Meet the Team")
    assert extraction.is_relevant_nav_link("Contact")
    assert not extraction.is_relevant_nav_link("Shop Now")
