"""
secrets_crypto.py — At-rest encryption for secret app_settings values
(SMTP/IMAP passwords, cloud LLM API keys).

Uses Fernet (symmetric, authenticated) with a key generated on first use and
stored in a local file next to the SQLite database — nothing to configure.

Graceful degradation: if the `cryptography` package isn't installed yet
(existing installs won't have it until they run `pip install -r
requirements.txt`), encrypt()/decrypt() become no-ops instead of crashing the
app — secrets stay plaintext exactly as they were before this module existed,
rather than breaking startup for anyone who hasn't updated dependencies.
"""
import logging
import os
from pathlib import Path
from typing import Optional

from .config import get_settings

logger = logging.getLogger(__name__)

_PREFIX = "enc:v1:"

try:
    from cryptography.fernet import Fernet, InvalidToken
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False
    logger.warning(
        "secrets_crypto: 'cryptography' package not installed — "
        "SMTP/IMAP passwords and API keys will be stored in plaintext. "
        "Run: pip install -r requirements.txt"
    )

_fernet: Optional["Fernet"] = None


def _key_path() -> Path:
    db_path = Path(get_settings().database_path)
    return db_path.parent / ".secret.key"


def _get_fernet() -> Optional["Fernet"]:
    global _fernet
    if not _CRYPTO_OK:
        return None
    if _fernet is not None:
        return _fernet

    path = _key_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            key = path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            path.write_bytes(key)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass  # best-effort on platforms without POSIX perms (Windows)
        _fernet = Fernet(key)
    except Exception as exc:
        logger.error("secrets_crypto: failed to load/create encryption key: %s", exc, exc_info=True)
        return None
    return _fernet


def encrypt(value: str) -> str:
    """Encrypt a value for storage. Returns the input unchanged if crypto is unavailable."""
    if not value:
        return value
    f = _get_fernet()
    if f is None:
        return value
    try:
        return _PREFIX + f.encrypt(value.encode("utf-8")).decode("ascii")
    except Exception as exc:
        logger.error("secrets_crypto: encrypt failed, storing plaintext: %s", exc)
        return value


def decrypt(value: Optional[str]) -> Optional[str]:
    """Decrypt a value read from storage. Values without the enc:v1: prefix
    (plaintext from before this module existed, or when crypto is unavailable)
    are returned as-is."""
    if not value or not value.startswith(_PREFIX):
        return value
    f = _get_fernet()
    if f is None:
        # Encrypted value but no key available (crypto package missing) —
        # can't recover it; surface as None rather than returning ciphertext.
        return None
    try:
        return f.decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, Exception) as exc:
        logger.error("secrets_crypto: decrypt failed: %s", exc)
        return None
