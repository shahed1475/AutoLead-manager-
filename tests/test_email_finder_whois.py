"""WHOIS registrar contacts must never become a lead's email."""
import types

import pytest

from backend.scrapers import email_finder


@pytest.mark.parametrize("email_domain,site,ok", [
    ("clinic.com", "clinic.com", True),
    ("clinic.com", "www.clinic.com", True),
    ("mail.clinic.com", "clinic.com", True),
    ("clinic.com", "book.clinic.com", True),
    ("registrarsafe.com", "dentalsignature.com", False),
    ("hostinger.com", "abduldentist.in", False),
    ("notclinic.com", "clinic.com", False),
    ("", "clinic.com", False),
])
def test_is_same_site(email_domain, site, ok):
    assert email_finder._is_same_site(email_domain, site) is ok


def _fake_whois(monkeypatch, emails):
    mod = types.SimpleNamespace(whois=lambda d: types.SimpleNamespace(emails=emails))
    monkeypatch.setitem(__import__("sys").modules, "whois", mod)


def test_whois_registrar_abuse_address_is_rejected(monkeypatch):
    _fake_whois(monkeypatch, ["abusecomplaints@registrarsafe.com", "domains@hostinger.com"])
    assert email_finder._whois_email("dentalsignature.com", lambda m: None) is None


def test_whois_same_domain_address_is_kept(monkeypatch):
    _fake_whois(monkeypatch, ["support@namebright.com", "owner@clinic.com"])
    assert email_finder._whois_email("clinic.com", lambda m: None) == "owner@clinic.com"


def test_abuse_prefixes_never_primary():
    assert email_finder._email_score("abuse-complaints@clinic.com", "clinic.com") < 0
