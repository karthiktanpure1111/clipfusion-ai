"""Queue payload snapshots: what exactly will be published for one clip on one platform."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.errors import ValidationFailed
from app.models import GeneratedClip, PublishingSchedule
from app.models.enums import Platform


class YouTubePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list, max_length=100)
    hashtags: list[str] = Field(default_factory=list, max_length=60)
    privacy: Literal["public", "unlisted", "private"] = "public"
    made_for_kids: bool = False

    @field_validator("title", "description")
    @classmethod
    def _no_angle_brackets(cls, v: str) -> str:
        if "<" in v or ">" in v:
            raise ValueError("YouTube does not allow '<' or '>'")
        return v


class InstagramPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caption: str = Field(default="", max_length=2200)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    share_to_feed: bool = True


class YouTubePayloadPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = None
    hashtags: list[str] | None = None
    privacy: Literal["public", "unlisted", "private"] | None = None
    made_for_kids: bool | None = None


class InstagramPayloadPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caption: str | None = Field(default=None, max_length=2200)
    hashtags: list[str] | None = None
    share_to_feed: bool | None = None


_MODELS: dict[str, type[BaseModel]] = {Platform.YOUTUBE: YouTubePayload, Platform.INSTAGRAM: InstagramPayload}


def validate_payload(platform: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a full payload for ``platform``; raises ValidationFailed with field errors."""
    model = _MODELS.get(platform)
    if model is None:
        raise ValidationFailed(f"Unsupported platform '{platform}'")
    try:
        return model.model_validate(payload).model_dump()
    except ValidationError as exc:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        raise ValidationFailed(f"Invalid {platform} publishing details", details=errors) from exc


def _clean_tags(values: Any) -> list[str]:
    return [str(v).strip().lstrip("#") for v in values or [] if str(v).strip().lstrip("#")]


def build_payload(clip: GeneratedClip, platform: str, *, schedule: PublishingSchedule | None = None,
                  overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Snapshot ``clip.social_metadata`` for ``platform`` (+ schedule defaults + overrides)."""
    meta = clip.social_metadata or {}
    if platform == Platform.YOUTUBE:
        yt = meta.get("youtube") or {}
        base: dict[str, Any] = {
            "title": (yt.get("title") or clip.title or "").replace("<", "").replace(">", "")[:100],
            "description": (yt.get("description") or "").replace("<", "").replace(">", "")[:5000],
            "tags": _clean_tags(yt.get("tags"))[:100], "hashtags": _clean_tags(yt.get("hashtags"))[:60],
            "privacy": (schedule.default_privacy if schedule else None) or yt.get("privacy") or "public",
            "made_for_kids": bool(yt.get("made_for_kids", False)),
        }
    elif platform == Platform.INSTAGRAM:
        ig = meta.get("instagram") or {}
        base = {"caption": (ig.get("caption") or clip.title or "")[:2200],
                "hashtags": _clean_tags(ig.get("hashtags"))[:30],
                "share_to_feed": bool(ig.get("share_to_feed", True))}
    else:
        raise ValidationFailed(f"Unsupported platform '{platform}'")
    if overrides:
        base.update({k: v for k, v in overrides.items() if v is not None})
    return validate_payload(platform, base)
