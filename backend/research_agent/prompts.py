"""
prompts.py — the two prompts LocalResearchLLM uses. Kept small and rigid on
purpose: a local 8B model's reliability at multi-turn tool choice drops fast
with prompt size, so the action-decision prompt gets a compact state summary,
not a page dump.
"""

ACTION_TOOL_DESCRIPTIONS = """\
- google_search: {"action": "google_search", "query": "..."} — search Google for businesses or people
- open_url: {"action": "open_url", "url": "..."} — open a specific URL (a search result or business website)
- extract_page_text: {"action": "extract_page_text"} — read the visible text of the current page
- find_links: {"action": "find_links", "keyword": "contact"} — list links on the current page, optionally filtered
- click: {"action": "click", "text": "Contact Us"} — click a link/button by its visible text
- scroll: {"action": "scroll", "direction": "down"} — scroll the current page
- go_back: {"action": "go_back"} — go back to the previous page
- open_new_tab: {"action": "open_new_tab", "url": "..."} — open a URL in a new tab
- screenshot: {"action": "screenshot"} — capture a screenshot for evidence
- save_evidence: {"action": "save_evidence", "field_name": "...", "value": "...", "confidence": 0.8, "status": "FOUND"} — record a finding you are confident about
- finish_research: {"action": "finish_research", "reason": "..."} — stop researching this business (found enough, or nothing more to try)
"""

ACTION_DECISION_PROMPT = """You are a research assistant deciding the SINGLE next action to research a business, one step at a time.

Current research state:
{state_summary}

Available actions (respond with EXACTLY one, as JSON):
{tools}

Rules:
- Choose the action most likely to find a MISSING required field.
- If you just opened a page and "Current page read yet" is "no", use extract_page_text — reading the page is how information is found.
- Use find_links only to locate a Contact / About / Team / Staff page, then open_url that page and extract_page_text it.
- To find an owner or manager, open the About/Team page or search '"Business Name" City owner' / 'practice manager'.
- Never invent information — only decide what to look at next.
- If required fields are already found, or you have tried enough without success, use finish_research.
- Respond with ONLY the JSON object, no prose, no markdown fences.

Example response:
{{"action": "google_search", "query": "\\"Business Name\\" \\"City\\" contact", "reason": "why", "confidence": 0.8}}
"""

FIELD_EXTRACTION_PROMPT = """Extract ONLY information that is EXPLICITLY present in this text. Do not guess or infer anything not written.

Business (if known): {business_name}
Fields still needed: {missing_fields}

Text:
{text}

Return ONLY strict JSON with any of these keys you can find explicitly stated (omit keys you cannot find — do NOT write "unknown" or make one up):
{{
  "management_contact_name": "full name of an owner/manager/lead dentist if explicitly stated, else omit",
  "management_title": "their exact title/role as written, else omit"
}}
Do not return email addresses or phone numbers — those are extracted separately.
"""
