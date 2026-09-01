"""
backend/research_agent — Browser Research Agent.

An independent subsystem (see docs/superpowers/specs/2026-08-25-browser-research-agent-design.md):
an iterative OBSERVE/DECIDE/ACT/VALIDATE loop where a local LLM (Ollama, via
ai_brain._call_llm_raw) chooses one controlled browser action at a time,
Playwright executes it against a real Chrome, and the result feeds back into
the next decision — until a lead's required fields are found (or research
budgets are exhausted).

Public entry point: agent.run_research_session(...).
"""
