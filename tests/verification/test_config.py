from backend.config import get_settings


def test_contact_verification_settings_exist_with_safe_defaults():
    s = get_settings()
    assert s.contact_verification_enabled is False
    assert s.verification_dns_timeout_seconds == 3.0
    assert s.verification_dns_lifetime_seconds == 5.0
    assert s.verification_max_leads_per_run == 2000
