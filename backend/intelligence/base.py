"""
base.py — shared types every research agent and the orchestrator depend on.

Deliberately minimal: no plugin registry, no discovery magic. Adding a new
agent later means implementing this Protocol and adding one line to the
orchestrator's ordered agent list — that's the whole "extensibility" story
this sub-project needs for 2 agents.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Protocol


@dataclass
class EvidenceItem:
    field_name: str
    source_type: str            # "website" | "ai_inference" | "heuristic"
    source_url: Optional[str]
    snippet: Optional[str]


@dataclass
class AgentResult:
    status: Literal["ok", "rejected", "failed"]
    data: Dict[str, Any] = field(default_factory=dict)
    evidence: List[EvidenceItem] = field(default_factory=list)
    confidence: float = 0.0
    reason: Optional[str] = None


class ResearchAgent(Protocol):
    name: str

    async def run(self, lead: Dict[str, Any], campaign: Optional[Dict[str, Any]] = None) -> AgentResult:
        ...
