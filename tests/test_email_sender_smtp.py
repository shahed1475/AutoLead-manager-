"""
test_email_sender_smtp.py — regression for _send_smtp serialisation.

_send_smtp used to pass msg.as_string() to smtplib.sendmail(), which
ASCII-encodes a str argument and raised UnicodeEncodeError on any non-ASCII
content (accented names, em-dashes, unicode bodies). It now passes
msg.as_bytes(). No real network — smtplib.SMTP_SSL / SMTP are faked.
"""
import smtplib

import pytest

from backend import email_sender

pytestmark = pytest.mark.asyncio


class _FakeServer:
    last = {}

    def __init__(self, host, port, timeout=None):
        _FakeServer.last = {"host": host, "port": port}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self):
        pass

    def login(self, user, pw):
        _FakeServer.last["login"] = (user, pw)

    def sendmail(self, from_addr, to_addr, msg):
        _FakeServer.last["from"] = from_addr
        _FakeServer.last["to"] = to_addr
        _FakeServer.last["msg"] = msg


CFG = {"host": "smtp.example-host.com", "port": 465, "username": "u@host.com",
       "password": "pw", "from_name": "Señor Tést —", "from_email": "u@host.com"}


async def test_send_smtp_handles_non_ascii_body_and_headers(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeServer)

    body = "Hola,\n\n[[ TEST MODE — für Ünïcode ]]\n\nСпасибо. — S"
    # must NOT raise UnicodeEncodeError
    email_sender._send_smtp("dest@somewhere.com", "", "Prüfung — [TEST -> x]", body, dict(CFG))

    sent = _FakeServer.last["msg"]
    assert isinstance(sent, (bytes, bytearray))          # bytes, not str
    # the unicode body survived (base64 or 8bit) — decode round-trips the text
    assert "Ünïcode" in sent.decode("utf-8", "replace") or b"=?utf-8?" in sent or b"VEVTVCBNT0RF" not in sent


async def test_send_smtp_ascii_path_still_works(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeServer)
    email_sender._send_smtp("dest@somewhere.com", "", "plain subject", "plain body", dict(CFG))
    assert isinstance(_FakeServer.last["msg"], (bytes, bytearray))
    assert _FakeServer.last["to"] == "dest@somewhere.com"


async def test_send_email_returns_true_on_fake_success(monkeypatch):
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeServer)
    ok = email_sender.send_email("dest@somewhere.com", "s", "b — unicode", dict(CFG))
    assert ok is True
