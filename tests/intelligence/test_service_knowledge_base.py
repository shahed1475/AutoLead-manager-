from backend.intelligence.service_knowledge_base import SERVICE_KNOWLEDGE_BASE, get_service

_EXPECTED_SERVICES = {
    "Custom Software", "SaaS Development", "AI & ML", "AI Automation",
    "Generative AI", "Agentic AI", "AI Chatbots", "RAG", "WhatsApp Automation",
    "CRM Development", "Workflow Automation", "Full-Stack Web Development",
    "E-commerce", "Custom Business Applications",
}


def test_all_required_services_present():
    names = {svc.name for svc in SERVICE_KNOWLEDGE_BASE}
    assert names == _EXPECTED_SERVICES


def test_no_duplicate_service_names():
    names = [svc.name for svc in SERVICE_KNOWLEDGE_BASE]
    assert len(names) == len(set(names))


def test_every_service_has_required_fields_populated():
    for svc in SERVICE_KNOWLEDGE_BASE:
        assert svc.name
        assert svc.problems_solved, f"{svc.name} missing problems_solved"
        assert svc.target_businesses, f"{svc.name} missing target_businesses"
        assert svc.use_cases, f"{svc.name} missing use_cases"
        assert svc.benefits, f"{svc.name} missing benefits"
        assert svc.recommend_when, f"{svc.name} missing recommend_when"
        assert isinstance(svc.do_not_recommend_when, list)


def test_recommend_when_keywords_are_lowercase():
    for svc in SERVICE_KNOWLEDGE_BASE:
        for kw in svc.recommend_when:
            assert kw == kw.lower(), f"{svc.name} has non-lowercase keyword: {kw}"
        for kw in svc.do_not_recommend_when:
            assert kw == kw.lower(), f"{svc.name} has non-lowercase exclusion keyword: {kw}"


def test_get_service_returns_matching_definition():
    svc = get_service("WhatsApp Automation")
    assert svc is not None
    assert svc.name == "WhatsApp Automation"


def test_get_service_returns_none_for_unknown():
    assert get_service("Not A Real Service") is None
