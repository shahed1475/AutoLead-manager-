"""
email_campaigns/deliverability.py — does the sending domain have the DNS
records inboxes check before trusting mail (SPF, DMARC, DKIM, MX)?

Deterministic DNS lookups only. A lookup that fails is 'unknown', never
'missing' — we don't claim a record is absent when we couldn't look.
DKIM selectors can't be listed through DNS, so the common ones are tried.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

DKIM_SELECTORS = ("google", "default", "selector1", "selector2", "k1", "k2", "s1", "s2",
                  "mail", "dkim", "zoho", "mxvault", "hostinger", "smtp")
SHARED_PROVIDERS = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
                    "yahoo.com", "icloud.com", "aol.com", "proton.me", "protonmail.com"}

Resolver = Callable[[str, str], List[str]]


class LookupFailed(Exception):
    """DNS couldn't answer (timeout, no network) — distinct from 'no record'."""


def _dns_resolve(name: str, rtype: str) -> List[str]:
    import dns.exception
    import dns.resolver
    try:
        answer = dns.resolver.resolve(name, rtype, lifetime=4.0)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except dns.exception.DNSException as exc:
        raise LookupFailed(str(exc)) from exc
    if rtype == "TXT":
        return [b"".join(r.strings).decode("utf-8", "replace") for r in answer]
    return [r.to_text() for r in answer]


def _res(status: str, detail: str) -> Dict[str, str]:
    return {"status": status, "detail": detail}


def _lookup(resolve: Resolver, name: str, rtype: str) -> Optional[List[str]]:
    try:
        return resolve(name, rtype)
    except LookupFailed:
        return None


def _spf(resolve: Resolver, domain: str) -> Dict[str, str]:
    txt = _lookup(resolve, domain, "TXT")
    if txt is None:
        return _res("unknown", "Couldn't look up the domain's records.")
    spf = [t for t in txt if t.lower().startswith("v=spf1")]
    if not spf:
        return _res("missing", "No SPF record: inboxes can't tell which servers may send for this domain.")
    rec = spf[0]
    if len(spf) > 1:
        return _res("weak", "More than one SPF record; inboxes treat that as an error. Merge them into one.")
    if "+all" in rec.lower() or rec.lower().rstrip().endswith(" all"):
        return _res("weak", f"SPF allows any server to send ({rec}). Use ~all or -all.")
    return _res("ok", rec)


def _dmarc(resolve: Resolver, domain: str) -> Dict[str, str]:
    txt = _lookup(resolve, f"_dmarc.{domain}", "TXT")
    if txt is None:
        return _res("unknown", "Couldn't look up the DMARC record.")
    rec = next((t for t in txt if t.lower().startswith("v=dmarc1")), None)
    if not rec:
        return _res("missing", "No DMARC record. Gmail and Yahoo expect one from bulk senders.")
    policy = next((p.split("=", 1)[1].strip().lower() for p in rec.split(";")
                   if p.strip().lower().startswith("p=")), "")
    if policy == "none":
        return _res("weak", "DMARC is in monitor-only mode (p=none). Move to p=quarantine once mail passes.")
    return _res("ok", rec)


def _dkim(resolve: Resolver, domain: str) -> Dict[str, str]:
    failures = 0
    for sel in DKIM_SELECTORS:
        txt = _lookup(resolve, f"{sel}._domainkey.{domain}", "TXT")
        if txt is None:
            failures += 1
            continue
        if any("p=" in t for t in txt):
            return _res("ok", f"DKIM key found (selector '{sel}').")
    if failures == len(DKIM_SELECTORS):
        return _res("unknown", "Couldn't look up DKIM keys.")
    return _res("missing", "No DKIM key under the common selectors. Turn on DKIM signing with your email provider "
                           "(if it uses an unusual selector, this check can't see it).")


def _mx(resolve: Resolver, domain: str) -> Dict[str, str]:
    mx = _lookup(resolve, domain, "MX")
    if mx is None:
        return _res("unknown", "Couldn't look up mail servers.")
    if not mx:
        return _res("missing", "No mail servers (MX): replies and bounces to this domain can't arrive.")
    return _res("ok", ", ".join(sorted(m.split()[-1].rstrip(".") for m in mx)))


def check_domain(domain: str, resolve: Optional[Resolver] = None) -> Dict[str, Any]:
    domain = (domain or "").strip().lower().lstrip("@")
    resolve = resolve or _dns_resolve
    shared = domain in SHARED_PROVIDERS
    out: Dict[str, Any] = {"domain": domain, "shared_provider": shared,
                           "spf": _spf(resolve, domain), "dmarc": _dmarc(resolve, domain),
                           "dkim": _dkim(resolve, domain), "mx": _mx(resolve, domain)}
    advice: List[str] = []
    if shared:
        # The provider owns these records and signs its own mail; nothing for
        # the user to fix, so don't report them as missing/weak.
        for key in ("spf", "dkim", "dmarc"):
            out[key] = _res("ok", f"Managed by {domain}; you can't change these records.")
        advice.append(f"{domain} addresses are signed by the provider, but they're limited to a few hundred "
                      "emails a day and cold email from them looks personal-only. Send campaigns from your "
                      "own domain for volume and trust.")
    else:
        for key, fix in (("spf", "Add an SPF record listing your email provider."),
                         ("dkim", "Turn on DKIM signing in your email provider's settings."),
                         ("dmarc", "Add a DMARC record, starting with p=none, then p=quarantine.")):
            if out[key]["status"] in ("missing", "weak"):
                advice.append(fix)
    out["advice"] = advice
    return out
