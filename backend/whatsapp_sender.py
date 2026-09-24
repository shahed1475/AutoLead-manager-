"""
whatsapp_sender.py — WhatsApp Desktop automation via pyautogui + pyperclip.

Strategy: keyboard-only navigation (no fragile pixel coordinates).
  1. Launch / focus WhatsApp Desktop   (subprocess + Win32 window search)
  2. Ctrl+N → New Chat dialog
  3. Paste phone number via clipboard   (handles +, spaces, unicode)
  4. Down + Enter → open first match
  5. Ctrl+V → paste message            (handles Unicode, emoji, newlines)
  6. Enter → send
  7. Random 2-4 s jitter               (human-pacing / anti-spam)

Public API (async):
  send_whatsapp(phone_number, message, config) -> bool   new low-level
  send_whatsapp_lead(lead)                               legacy high-level
  send_followup_whatsapp(lead)                           legacy high-level
"""

import asyncio
import ctypes
import ctypes.wintypes
import logging
import os
import random
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from . import database as db
from .config import get_settings
from .log_stream import emit as _emit

_env   = get_settings()
logger = logging.getLogger(__name__)

# Serialise all WA automations — pyautogui controls the desktop globally
_wa_lock: asyncio.Lock = asyncio.Lock()


# ── Config helpers ─────────────────────────────────────────────────────────────


async def _wa_cfg() -> Dict[str, Any]:
    """Read DB settings (live UI values) with .env fallback."""
    stored = await db.get_all_settings()
    return {
        "wait_time":     int(stored.get("whatsapp_wait_time") or _env.whatsapp_wait_time),
        "close_tab":     stored.get("whatsapp_close_tab", "true").lower() != "false",
        "custom_path":   stored.get("whatsapp_path", ""),
    }


# ── Phone normalisation ────────────────────────────────────────────────────────


def _normalize_phone(phone: str) -> str:
    """
    Normalise to E.164 format (+[country][number]).

    Rules (in priority order):
      starts with '+'     → strip non-digits, re-add '+'
      10 digits           → US/CA: prepend +1
      11 digits, starts 1 → US long-form: prepend +
      anything else       → trust as-is, prepend + if missing
    """
    stripped = phone.strip()
    has_plus = stripped.startswith("+")
    digits   = re.sub(r"\D", "", stripped)

    if has_plus:
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits[0] == "1":
        return "+" + digits
    return "+" + digits


# ── WhatsApp Desktop launchers ─────────────────────────────────────────────────


def _candidate_paths(custom: str) -> List[str]:
    """Return ordered list of WhatsApp Desktop executable paths to try."""
    home = os.path.expanduser("~")
    paths = [
        os.path.join(home, "AppData", "Local", "WhatsApp", "WhatsApp.exe"),
        os.path.join(home, "AppData", "Local", "Microsoft", "WindowsApps", "WhatsApp.exe"),
        r"C:\Program Files\WhatsApp\WhatsApp.exe",
        r"C:\Program Files (x86)\WhatsApp\WhatsApp.exe",
    ]
    return ([custom] if custom else []) + paths


def _launch_whatsapp(config: Dict[str, Any]) -> bool:
    """
    Start WhatsApp Desktop or bring it to the foreground.

    Tries (in order):
      1. URL protocol: whatsapp://  — works if WA Desktop is registered
      2. Known executable paths
      3. 'WhatsApp' by name in system PATH

    Returns True if a launch attempt was made, False if nothing found.
    """
    # 1. URL protocol (handles Store installs + standard installs)
    if sys.platform == "win32":
        try:
            os.startfile("whatsapp://")
            _emit("INFO", "WHATSAPP", "Launched via whatsapp:// protocol")
            return True
        except OSError:
            pass
    elif sys.platform == "darwin":
        try:
            subprocess.Popen(["open", "whatsapp://"])
            return True
        except FileNotFoundError:
            pass
    else:
        try:
            subprocess.Popen(["xdg-open", "whatsapp://"])
            return True
        except FileNotFoundError:
            pass

    # 2. Known paths
    for path in _candidate_paths(config.get("custom_path", "")):
        if path and os.path.isfile(path):
            subprocess.Popen([path])
            _emit("INFO", "WHATSAPP", f"Launched: {path}")
            return True

    # 3. By name
    for name in ("WhatsApp", "WhatsApp.exe"):
        try:
            subprocess.Popen([name])
            _emit("INFO", "WHATSAPP", f"Launched by name: {name}")
            return True
        except FileNotFoundError:
            continue

    _emit("ERROR", "WHATSAPP",
          "WhatsApp Desktop not found. Install from https://www.whatsapp.com/download")
    return False


def _focus_whatsapp_window() -> bool:
    """
    Bring an open WhatsApp Desktop window to the foreground.
    Uses Win32 API via ctypes — no extra packages required.
    Returns True if a WhatsApp window was found and raised.
    """
    if sys.platform != "win32":
        return False

    user32 = ctypes.windll.user32

    found: List[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def _enum(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if "WhatsApp" in buf.value:
                    found.append(hwnd)
        return True

    user32.EnumWindows(_enum, 0)

    if not found:
        return False

    hwnd = found[0]
    user32.ShowWindow(hwnd, 9)          # SW_RESTORE — un-minimise
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    return True


# ── Core pyautogui automation (sync, runs in asyncio.to_thread) ────────────────


def _send_whatsapp_desktop(phone: str, message: str, config: Dict[str, Any]) -> bool:
    """
    Automate WhatsApp Desktop to send `message` to `phone`.

    All navigation is keyboard-only — no screen coordinates needed.
    pyperclip handles Unicode, emoji, and multi-line messages via clipboard paste.

    Returns True on success, False on any detected failure.
    """
    try:
        import pyautogui   # noqa: PLC0415
        import pyperclip   # noqa: PLC0415
    except (ImportError, SystemExit) as exc:
        # SystemExit: pyautogui's mouseinfo dependency calls sys.exit() on
        # Linux without tkinter — uncaught, that kills the whole server.
        logger.error("Missing dependency: %s — run: pip install pyautogui pyperclip", exc)
        _emit("ERROR", "WHATSAPP", f"Missing dependency: {exc}")
        return False

    pyautogui.FAILSAFE = True    # move mouse to top-left corner to abort
    pyautogui.PAUSE    = 0.08    # small inter-action delay

    wait_time = int(config.get("wait_time", 3))

    # ── 1. Focus existing window or launch fresh ───────────────────────────────
    _emit("INFO", "WHATSAPP", f"Preparing WhatsApp Desktop for {phone}…")

    if _focus_whatsapp_window():
        _emit("INFO", "WHATSAPP", "Existing WhatsApp window brought to foreground")
        time.sleep(0.8)
    else:
        if not _launch_whatsapp(config):
            return False
        _emit("INFO", "WHATSAPP", f"Waiting {wait_time}s for WhatsApp to load…")
        time.sleep(wait_time)

        # Second focus attempt after launch
        for _ in range(5):
            if _focus_whatsapp_window():
                break
            time.sleep(1)
        else:
            _emit("WARNING", "WHATSAPP",
                  "Could not confirm WhatsApp window — attempting anyway")

    time.sleep(0.5)

    # ── 2. Open New Chat dialog ────────────────────────────────────────────────
    _emit("INFO", "WHATSAPP", "Opening New Chat dialog (Ctrl+N)…")
    pyautogui.hotkey("ctrl", "n")
    time.sleep(1.5)         # wait for dialog animation

    # ── 3. Paste phone number into search box ─────────────────────────────────
    #    Use clipboard paste — handles '+' and any keyboard layout edge cases
    _emit("INFO", "WHATSAPP", f"Searching for {phone}…")
    pyperclip.copy(phone)
    pyautogui.hotkey("ctrl", "a")   # clear any pre-filled text
    time.sleep(0.2)
    pyautogui.hotkey("ctrl", "v")   # paste phone number
    time.sleep(2.2)                 # wait for WA to resolve and render results

    # ── 4. Select first result → open chat ────────────────────────────────────
    _emit("INFO", "WHATSAPP", "Selecting result and opening chat…")
    pyautogui.press("down")         # highlight first search result
    time.sleep(0.35)
    pyautogui.press("enter")        # open the chat
    time.sleep(1.6)                 # wait for chat pane to load and focus message box

    # ── 5. Paste message via clipboard ────────────────────────────────────────
    #    Never use typewrite() for messages — it garbles Unicode and special chars
    _emit("INFO", "WHATSAPP", "Pasting message…")
    pyperclip.copy(message)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.45)

    # ── 6. Send ───────────────────────────────────────────────────────────────
    pyautogui.press("enter")
    _emit("INFO", "WHATSAPP", f"Message sent → {phone}")

    # ── 7. Anti-spam jitter ───────────────────────────────────────────────────
    jitter = random.uniform(2.0, 4.0)
    time.sleep(jitter)

    return True


# ══ PUBLIC API ══════════════════════════════════════════════════════════════════


async def send_whatsapp(phone_number: str, message: str, config: Dict[str, Any]) -> bool:
    """
    Send a WhatsApp message via WhatsApp Desktop automation.

    Acquires a process-wide lock so only one automation runs at a time
    (pyautogui controls the global keyboard/focus — concurrent calls would conflict).

    Args:
        phone_number: any common format — normalised to E.164 internally
        message:      text to send (Unicode, emoji, newlines all supported)
        config:       dict with optional keys:
                        wait_time   int  — seconds to wait for WA to load (default 3)
                        custom_path str  — explicit path to WhatsApp.exe

    Returns:
        True on success, False on failure (errors logged + pushed to log_stream)
    """
    from . import edition
    if edition.is_client():
        _emit("ERROR", "WHATSAPP", "WhatsApp sending isn't available in client workspaces")
        return False
    if not phone_number or not phone_number.strip():
        _emit("ERROR", "WHATSAPP", "send_whatsapp called with empty phone_number")
        return False
    if not message or not message.strip():
        _emit("ERROR", "WHATSAPP", "send_whatsapp called with empty message")
        return False

    if sys.platform != "win32":
        msg = "WhatsApp Desktop automation is Windows-only — use EMAIL on this machine"
        logger.warning(msg)
        _emit("ERROR", "WHATSAPP", msg)
        return False

    normalized = _normalize_phone(phone_number)

    async with _wa_lock:            # enforce single-writer serialisation
        try:
            return await asyncio.to_thread(
                _send_whatsapp_desktop, normalized, message, config
            )
        except Exception as exc:
            exc_name = type(exc).__name__
            if "FailSafe" in exc_name:
                # pyautogui.FailSafeException — user moved mouse to screen corner
                _emit("WARNING", "WHATSAPP",
                      "Fail-safe triggered — mouse moved to top-left corner. Automation aborted.")
                return False
            logger.error("send_whatsapp error for %s: %s", phone_number, exc, exc_info=True)
            _emit("ERROR", "WHATSAPP", f"{exc_name}: {exc}")
            return False


# ── Legacy high-level API (called by scheduler.py and campaigns.py) ────────────


_MSG_GARBAGE_MARKERS = ("[Generation failed", "[Message generation failed", "[Subject —", "[Body —")


async def send_whatsapp_lead(lead: Dict[str, Any]) -> None:
    """
    Send WhatsApp outreach to a lead.
    Reads config from DB. Raises RuntimeError on delivery failure.
    """
    phone = lead.get("phone")
    if not phone:
        raise ValueError(f"Lead {lead.get('id')} has no phone number")

    message = lead.get("ai_whatsapp_msg") or ""
    if not message or any(message.startswith(m) for m in _MSG_GARBAGE_MARKERS):
        biz     = lead.get("business_name") or "your business"
        niche   = lead.get("niche")         or "your industry"
        message = (
            f"Hi! I came across {biz} and wanted to reach out. "
            f"We help {niche} businesses grow with digital marketing. "
            "Would you be open to a quick chat?"
        )

    cfg = await _wa_cfg()
    _emit("INFO", "WHATSAPP", f"Sending → {lead.get('business_name', phone)}")

    ok = await send_whatsapp(phone, message, cfg)
    if not ok:
        raise RuntimeError(f"WhatsApp delivery failed for {phone}")


async def send_followup_whatsapp(lead: Dict[str, Any]) -> None:
    """Send a WhatsApp follow-up to a lead that hasn't responded."""
    phone = lead.get("phone")
    if not phone:
        raise ValueError(f"Lead {lead.get('id')} has no phone number")

    message = lead.get("ai_followup_msg") or ""
    if not message or any(message.startswith(m) for m in _MSG_GARBAGE_MARKERS):
        biz     = lead.get("business_name") or "your business"
        message = (
            f"Hi again! Just wanted to follow up on my previous message about {biz}. "
            "Would you be open to a quick chat about how we can help you grow? "
            "Happy to keep it brief!"
        )

    cfg = await _wa_cfg()
    _emit("INFO", "WHATSAPP", f"Follow-up → {lead.get('business_name', phone)}")

    ok = await send_whatsapp(phone, message, cfg)
    if not ok:
        raise RuntimeError(f"WhatsApp follow-up failed for {phone}")
