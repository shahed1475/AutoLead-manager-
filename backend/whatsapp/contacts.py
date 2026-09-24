"""
contacts.py — WhatsApp campaign contacts from an uploaded file (CSV / XLSX).

The manual alternative to picking leads automatically: the owner uploads a
contact list, HOM finds the phone column (and optional name / company / city /
niche), fixes up numbers with a default country code, and reports what's
usable before anything is sent. Imported contacts become leads (source
WHATSAPP_IMPORT) so replies, automatic answers, opt-outs and the monitor work
the same as for every other lead. People marked Do not contact — or already in
a conversation — are never added to a campaign.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .. import database as db
from ..email_campaigns.file_import import _rows_from_csv, _rows_from_xlsx
from .service import CAMPAIGN_OK_STATUSES, digits, find_lead_by_whatsapp

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000

_HEADERS: Dict[str, List[str]] = {
    "phone": ["phone", "phone number", "mobile", "mobile number", "cell", "whatsapp", "whatsapp number",
              "number", "contact", "contact number", "tel", "telephone", "phone no", "mobile no"],
    "name": ["name", "full name", "contact name", "first name", "person", "owner", "owner name"],
    "company": ["company", "company name", "business", "business name", "organization", "organisation", "shop", "store"],
    "city": ["city", "location", "town", "area"],
    "niche": ["niche", "industry", "sector", "category", "type"],
}


class ContactFileError(ValueError):
    pass


@dataclass
class Parsed:
    contacts: List[Dict[str, Any]] = field(default_factory=list)   # {phone, name, company, city, niche, row}
    invalid: List[Dict[str, Any]] = field(default_factory=list)    # {row, value, reason}
    duplicates: int = 0
    columns: Dict[str, str] = field(default_factory=dict)          # canonical -> header in the file


def _norm(h: Any) -> str:
    return re.sub(r"[\s_\-.]+", " ", str(h or "")).strip().lower()


def normalize_phone(raw: Any, country_code: str = "") -> Optional[str]:
    """→ '+<digits>' (E.164) or None. Local numbers get the default country code."""
    text = str(raw or "").strip()
    if text.endswith(".0") and text[:-2].isdigit():          # Excel stored it as a number
        text = text[:-2]
    d = digits(text)
    cc = digits(country_code)
    if not d:
        return None
    if text.startswith("+"):
        pass
    elif d.startswith("00"):
        d = d[2:]
    elif cc and d.startswith("0"):
        local = d.lstrip("0")
        if len(local) < 7:                                    # too short to be a real number
            return None
        d = cc + local
    elif cc and len(d) <= 10 and not d.startswith(cc):
        if len(d) < 7:
            return None
        d = cc + d
    return f"+{d}" if 8 <= len(d) <= 15 else None


def parse_file(filename: str, data: bytes, country_code: str = "") -> Parsed:
    if not data:
        raise ContactFileError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ContactFileError("The file is too big — keep it under 5 MB.")
    name = (filename or "").lower()
    if name.endswith(".xls"):
        raise ContactFileError("Old .xls files aren't supported — save it as .xlsx or .csv.")
    try:
        rows = _rows_from_xlsx(data) if name.endswith(".xlsx") else _rows_from_csv(data)
    except Exception as exc:  # noqa: BLE001
        raise ContactFileError(f"Couldn't read the file ({type(exc).__name__}). Use .xlsx or .csv.") from exc
    rows = [r for r in rows if any(str(c or "").strip() for c in r)]
    if not rows:
        raise ContactFileError("The file has no rows.")

    header = [_norm(h) for h in rows[0]]
    idx: Dict[str, int] = {}
    out = Parsed()
    for canon, names in _HEADERS.items():
        for i, h in enumerate(header):
            if h in names and i not in idx.values():
                idx[canon] = i
                out.columns[canon] = str(rows[0][i])
                break
    body = rows[1:]
    if "phone" not in idx:
        # No header row? Accept a single column of numbers.
        if all(normalize_phone(r[0], country_code) or not str(r[0] or "").strip() for r in rows[:5]):
            idx, body = {"phone": 0}, rows
            out.columns = {"phone": "(first column)"}
        else:
            raise ContactFileError("Couldn't find a phone column — name it “Phone”, “Mobile” or “WhatsApp”.")
    if len(body) > MAX_ROWS:
        raise ContactFileError(f"Too many rows — at most {MAX_ROWS} per file.")

    seen = set()
    for n, r in enumerate(body, start=2 if body is not rows else 1):
        def cell(key: str) -> str:
            i = idx.get(key)
            return str(r[i]).strip() if i is not None and i < len(r) and r[i] is not None else ""
        raw_phone = cell("phone")
        phone = normalize_phone(raw_phone, country_code)
        if not phone:
            if raw_phone:
                out.invalid.append({"row": n, "value": raw_phone, "reason": "not a valid phone number"})
            continue
        if phone in seen:
            out.duplicates += 1
            continue
        seen.add(phone)
        out.contacts.append({"phone": phone, "name": cell("name")[:120], "company": cell("company")[:160],
                             "city": cell("city")[:80], "niche": cell("niche")[:80], "row": n})
    return out


async def check_and_import(parsed: Parsed, save: bool) -> Dict[str, Any]:
    """Sort the contacts into usable / opted out / already talking to you.
    With save=True, create leads for new numbers and return their ids."""
    ready, opted_out, busy, lead_ids, new = [], [], [], [], 0
    for c in parsed.contacts:
        lead = await find_lead_by_whatsapp(digits(c["phone"]))
        if lead and lead["status"] == "DO_NOT_CONTACT":
            opted_out.append(c)
            continue
        if lead and lead["status"] not in CAMPAIGN_OK_STATUSES:
            busy.append(c)
            continue
        ready.append(c)
        if not save:
            continue
        if lead:
            lead_ids.append(lead["id"])
            if c["name"] and not lead.get("contact_name"):
                await db.update_lead(lead["id"], {"contact_name": c["name"]})
            continue
        lid = await db.create_lead({
            "business_name": c["company"] or c["name"] or c["phone"], "phone": c["phone"],
            "contact_name": c["name"] or None, "city": c["city"] or None, "niche": c["niche"] or None,
            "source": "WHATSAPP_IMPORT"})
        lead_ids.append(lid)
        new += 1
    return {
        "ready": len(ready), "invalid": len(parsed.invalid), "duplicates": parsed.duplicates,
        "opted_out": len(opted_out), "already_talking": len(busy), "new_leads": new,
        "columns": parsed.columns, "preview": ready[:8], "invalid_rows": parsed.invalid[:8],
        "lead_ids": lead_ids,
    }
