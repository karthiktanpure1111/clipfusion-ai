"""Alembic environment: uses ``settings.database_url`` and the ORM metadata of ``app.models``.

Custom column types are rendered as the application's own types so migrations keep
JSONB on PostgreSQL (``JSONType``) and timezone-aware UTC datetimes (``UTCDateTime``).
SQLite uses batch mode (ALTER TABLE emulation).
"""

from __future__ import annotations

from logging.config import fileConfig
from typing import Any

from sqlalchemy import JSON, engine_from_config, pool

import app.models  # noqa: F401 - registers every table on Base.metadata
from alembic import context
from app.core.config import get_settings
from app.core.database import Base, UTCDateTime

config = context.config
if config.config_file_name is not None and not config.attributes.get("skip_logging_config"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    return config.attributes.get("database_url") or get_settings().database_url


def render_item(type_: str, obj: Any, autogen_context: Any) -> str | bool:
    """Render the app's custom types by name (imported in the migration file)."""
    if type_ == "type":
        if isinstance(obj, UTCDateTime):
            autogen_context.imports.add("from app.core.database import JSONType, UTCDateTime  # noqa: F401")
            return "UTCDateTime()"
        if isinstance(obj, JSON) and "postgresql" in (getattr(obj, "_variant_mapping", None) or {}):
            autogen_context.imports.add("from app.core.database import JSONType, UTCDateTime  # noqa: F401")
            return "JSONType"
    return False


def _configure_kwargs(url: str) -> dict[str, Any]:
    return {"target_metadata": target_metadata, "compare_type": True, "render_item": render_item,
            "render_as_batch": url.startswith("sqlite")}


def run_migrations_offline() -> None:
    url = _url()
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"}, **_configure_kwargs(url))
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _url()
    connectable = engine_from_config({"sqlalchemy.url": url}, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, **_configure_kwargs(url))
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
