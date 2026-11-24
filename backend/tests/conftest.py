"""Shared pytest fixtures.

Tests run against a throw-away SQLite database, local storage under a temp dir and the
in-process worker. Set ``CF_TEST_DATABASE_URL`` to run the suite against PostgreSQL.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="cf-tests-"))
os.environ.setdefault("ENVIRONMENT", "test")
os.environ["DATABASE_URL"] = os.environ.get("CF_TEST_DATABASE_URL", f"sqlite:///{(_TMP / 'test.db').as_posix()}")
os.environ["AUTH_REQUIRED"] = "true"  # no-login mode has its own tests
os.environ["WORKER_MODE"] = "local"
os.environ["AUTO_SCHEDULE_DEFAULT"] = "false"  # only the auto-schedule tests switch it on
# every test shares one database and one Pacific quota day: the quota tests lower the upload limit themselves
os.environ["YOUTUBE_DAILY_UPLOAD_LIMIT"] = "100000"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["LOCAL_STORAGE_DIR"] = str(_TMP / "storage")
os.environ["TEMP_DIR"] = str(_TMP / "tmp")
os.environ.setdefault("SECRET_KEY", "test-secret-key-" + "x" * 40)
os.environ.setdefault("TOKEN_ENCRYPTION_KEYS", "q3ZQnmnTqAqvG4cD0bW9nJxH0m3ZtQ0vX6c3ZzJ7m1E=")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("BACKEND_PUBLIC_URL", "http://testserver")

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.core.database import Base, SessionLocal, get_engine  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.models import User  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _create_schema() -> Iterator[None]:
    engine = get_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db() -> Iterator:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def user(db) -> User:
    u = User(email=f"user-{uuid.uuid4().hex[:8]}@example.com", password_hash=hash_password("Sup3rSecret!pw"),
             full_name="Test User", timezone="UTC", settings={})
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def other_user(db) -> User:
    u = User(email=f"other-{uuid.uuid4().hex[:8]}@example.com", password_hash=hash_password("Sup3rSecret!pw"),
             full_name="Other User", timezone="UTC", settings={})
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.fixture()
def client() -> Iterator:
    """FastAPI TestClient (imports the app lazily so engine-only tests don't need it)."""
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def tmp_media_dir() -> Path:
    d = _TMP / "media"
    d.mkdir(parents=True, exist_ok=True)
    return d
