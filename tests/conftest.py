"""
conftest.py — must set DATABASE_PATH before any `backend.*` module is imported,
because backend/database.py captures DB_PATH as a module-level global at import
time (from backend.config.get_settings(), which is @lru_cache'd). This module
runs before any test file in this directory tree is collected, so it's the one
safe place to do this.
"""
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_TEST_DB_PATH = REPO_ROOT / "backend" / "data" / "test_autolead.db"
os.environ["DATABASE_PATH"] = str(_TEST_DB_PATH)

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from backend import database as db  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """slowapi's Limiter keeps per-IP counters in process memory. Without a
    reset between tests they accumulate across a run and a later test on a
    rate-limited endpoint gets a spurious 429. Clear it before every test."""
    try:
        from backend.rate_limit import limiter
        limiter.reset()
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _isolated_workspace_files(tmp_path, monkeypatch):
    """Client workspaces talk to the host supervisor through files. Tests must
    never write the real control file or read the real key/status."""
    from backend.portal import config as portal_config, workspaces
    monkeypatch.setattr(workspaces, "CONTROL_FILE", tmp_path / "workspaces.json")
    monkeypatch.setattr(workspaces, "STATUS_FILE", tmp_path / "status.json")
    monkeypatch.setattr(workspaces, "KEY_FILE", tmp_path / "workspace.key")
    monkeypatch.setattr(portal_config, "LINK_FILE", tmp_path / "portal-link.txt")
    yield


@pytest_asyncio.fixture
async def clean_db():
    """Function-scoped: wipe and reinitialize the test SQLite DB before each test.
    DB_PATH itself is fixed (set once above); the file at that path is recreated
    per test so tests never see each other's data."""
    if _TEST_DB_PATH.exists():
        _TEST_DB_PATH.unlink()
    await db.init_db()
    yield db
    if _TEST_DB_PATH.exists():
        _TEST_DB_PATH.unlink()
