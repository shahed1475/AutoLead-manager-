from backend.intelligence.base import AgentResult, EvidenceItem


def test_agent_result_defaults():
    result = AgentResult(status="ok")
    assert result.data == {}
    assert result.evidence == []
    assert result.confidence == 0.0
    assert result.reason is None


def test_agent_result_with_values():
    ev = EvidenceItem(field_name="industry", source_type="website",
                       source_url="https://x.com", snippet="hello")
    result = AgentResult(status="rejected", data={"a": 1}, evidence=[ev],
                          confidence=0.3, reason="missing name")
    assert result.evidence[0].field_name == "industry"
    assert result.reason == "missing name"
