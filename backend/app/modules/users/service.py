"""User profile, settings, password and account deletion."""

from __future__ import annotations

import logging
import shutil
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AuthError, ValidationFailed
from app.core.security import hash_password, validate_password_strength, verify_password
from app.core.storage import get_storage
from app.models import FramingPreset, UploadSession, User
from app.modules.users.schemas import PasswordChange, UserOut, UserSettings, UserUpdate

log = logging.getLogger(__name__)


def validate_timezone(tz: str) -> str:
    """Return ``tz`` if it is a valid IANA timezone name, else raise ValidationFailed."""
    name = (tz or "").strip()
    if not name:
        raise ValidationFailed("Timezone is required", code="invalid_timezone")
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationFailed(f"Unknown timezone '{name}'", code="invalid_timezone") from exc
    return name


def read_settings(user: User) -> UserSettings:
    """Stored settings merged over defaults; invalid stored values fall back to defaults."""
    raw = dict(user.settings or {})
    try:
        return UserSettings.model_validate(raw)
    except ValidationError as exc:
        bad = {str(e["loc"][0]) for e in exc.errors() if e.get("loc")} or             {"default_min_duration", "default_max_duration"}  # cross-field check failed
        log.warning("User %s has invalid stored settings %s; using defaults for them", user.id, sorted(bad))
        return UserSettings.model_validate({k: v for k, v in raw.items() if k not in bad})


def user_out(user: User) -> UserOut:
    """Serialise a user for the API."""
    return UserOut(id=user.id, email=user.email, full_name=user.full_name, avatar_url=user.avatar_url,
                   timezone=user.timezone, role=user.role, settings=read_settings(user),
                   has_password=bool(user.password_hash), google_linked=bool(user.google_sub),
                   created_at=user.created_at)


def _check_presets(db: Session, user: User, values: dict) -> None:
    fid = values.get("default_framing_preset_id")
    if fid is not None:
        preset = db.get(FramingPreset, fid)
        if preset is None or preset.user_id != user.id:
            raise ValidationFailed("Framing preset not found", code="invalid_preset")


def update_user(db: Session, user: User, data: UserUpdate) -> User:
    """Apply a profile PATCH; settings are merged into the stored JSON."""
    if data.full_name is not None:
        user.full_name = data.full_name.strip()
    if data.timezone is not None:
        user.timezone = validate_timezone(data.timezone)
    if data.settings is not None:
        patch = data.settings.model_dump(exclude_unset=True, mode="json")
        _check_presets(db, user, data.settings.model_dump(exclude_unset=True))
        merged = {k: v for k, v in {**(user.settings or {}), **patch}.items()
                  if k != "default_subtitle_preset_id"}  # subtitles were removed (API v2)
        try:  # validate the merged result before storing it
            UserSettings.model_validate(merged)
        except ValidationError as exc:
            raise ValidationFailed("Invalid settings", details=exc.errors(include_url=False,
                                                                          include_context=False)) from exc
        user.settings = merged
    db.commit()
    db.refresh(user)
    return user


def change_password(db: Session, user: User, data: PasswordChange) -> None:
    """Set a new password. The current one is required when the account already has one."""
    if user.password_hash:
        if not data.current_password or not verify_password(data.current_password, user.password_hash):
            raise AuthError("Current password is incorrect", code="invalid_password", status_code=403)
    validate_password_strength(data.new_password)
    user.password_hash = hash_password(data.new_password)
    db.commit()
    log.info("Credentials updated for user %s", user.id)


def delete_user(db: Session, user: User) -> None:
    """Delete the account, all of its rows (FK cascades) and every stored file."""
    uid = user.id
    session_ids = list(db.scalars(select(UploadSession.id).where(UploadSession.user_id == uid)))
    get_storage().delete_prefix(f"users/{uid}/")
    upload_root = get_settings().temp_dir / "uploads"
    for sid in session_ids:
        shutil.rmtree(upload_root / str(sid), ignore_errors=True)
    db.delete(user)
    db.commit()
    log.info("Deleted user %s and their stored media", uid)
