"""
enrichment/structured_data.py — the schema.org facts a website publishes about
itself in JSON-LD (<script type="application/ld+json">): the business's own
phone, email, address and social profiles, and the people it names.

Deterministic: no AI, no guessing. A person counts only when the business
lists them (founder / owner / employee / member) or they carry a job title —
a blog post's author isn't staff. Unreadable blocks are skipped.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

_BLOCK = re.compile(r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S)
_NOT_ORG = {"person", "webpage", "website", "breadcrumblist", "listitem", "imageobject", "searchaction",
            "postaladdress", "geocoordinates", "article", "blogposting", "faqpage", "question", "answer",
            "readaction", "entrypoint", "offer", "aggregaterating", "review", "rating", "videoobject",
            "openinghoursspecification", "contactpoint", "place", "collectionpage", "itemlist"}
_PEOPLE_KEYS = (("founder", "founder"), ("founders", "founder"), ("owner", "owner"),
                ("employee", "employee"), ("employees", "employee"), ("member", "member"), ("members", "member"))


def _types(node: Dict[str, Any]) -> List[str]:
    t = node.get("@type") or []
    return [str(x) for x in (t if isinstance(t, list) else [t])]


def _list(v: Any) -> List[Any]:
    return v if isinstance(v, list) else ([] if v is None else [v])


def _walk(v: Any, out: List[Dict[str, Any]]) -> None:
    if isinstance(v, dict):
        out.append(v)
        for x in v.values():
            _walk(x, out)
    elif isinstance(v, list):
        for x in v:
            _walk(x, out)


def _nodes(html: str) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    for raw in _BLOCK.findall(html or ""):
        text = raw.strip().removeprefix("<!--").removesuffix("-->").strip()
        text = text.removeprefix("//<![CDATA[").removesuffix("//]]>").strip()
        try:
            _walk(json.loads(text), nodes)
        except (ValueError, TypeError):
            continue
    return nodes


def _email(v: Any) -> Optional[str]:
    s = str(v or "").strip()
    s = s[7:] if s.lower().startswith("mailto:") else s
    return s.lower() if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s) else None


def _address(a: Any) -> Optional[str]:
    if isinstance(a, str):
        return a.strip() or None
    if not isinstance(a, dict):
        return None
    country = a.get("addressCountry")
    country = country.get("name") if isinstance(country, dict) else country
    parts = [a.get("streetAddress"), a.get("addressLocality"), a.get("addressRegion"), a.get("postalCode"), country]
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip()) or None


def _uniq(items: List[Optional[str]]) -> List[str]:
    return list(dict.fromkeys(i for i in items if i))


def extract(html: Optional[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"name": None, "types": [], "telephones": [], "emails": [], "same_as": [],
                           "address": None, "people": []}
    nodes = _nodes(html or "")
    if not nodes:
        return out
    by_id = {n["@id"]: n for n in nodes if isinstance(n.get("@id"), str) and len(n) > 1}

    def resolve(n: Any) -> Any:
        return by_id.get(n["@id"], n) if isinstance(n, dict) and set(n) == {"@id"} else n

    org = next((n for n in nodes if _types(n) and not {t.lower() for t in _types(n)} & _NOT_ORG
                and n.get("name") and any(k in n for k in ("telephone", "address", "email", "founder",
                                                           "employee", "sameAs", "owner", "member"))), None)
    people: Dict[str, Dict[str, Any]] = {}

    def add(p: Any, role: Optional[str]) -> None:
        p = resolve(p)
        if not isinstance(p, dict) or "Person" not in _types(p) or not p.get("name"):
            return
        name = str(p["name"]).strip()
        if name.lower() in people:
            return
        title = ", ".join(str(t).strip() for t in _list(p.get("jobTitle")) if str(t).strip()) or None
        people[name.lower()] = {"name": name, "job_title": title,
                                "role": role, "email": _email(p.get("email")),
                                "telephone": str(p["telephone"]).strip() if p.get("telephone") else None}

    if org:
        out["name"] = str(org["name"]).strip()
        out["types"] = _types(org)
        out["telephones"] = _uniq([str(t).strip() for t in _list(org.get("telephone"))])
        out["emails"] = _uniq([_email(e) for e in _list(org.get("email"))])
        out["same_as"] = _uniq([str(u).strip() for u in _list(org.get("sameAs")) if str(u).startswith("http")])
        out["address"] = _address(resolve(org.get("address")))
        for key, role in _PEOPLE_KEYS:
            for p in _list(org.get(key)):
                add(p, role)
    for n in nodes:                                   # anyone else the site gives a job title
        if "Person" in _types(n) and n.get("jobTitle"):
            add(n, None)
    out["people"] = list(people.values())
    return out
