"""Provider registry selected by environment (``.env``) settings.

=========================  ==================================================================
``TRANSCRIPTION_PROVIDER``  ``faster_whisper`` (default, local)
``HIGHLIGHT_RANKER``        ``auto`` (default) | ``signals`` | ``claude``
``METADATA_PROVIDER``       ``auto`` (default) | ``extractive`` | ``claude``
=========================  ==================================================================

``auto`` keeps the previous behaviour: the free local path (``signals`` / ``extractive``)
unless ``ANTHROPIC_API_KEY`` is configured, in which case Claude is used (and still only when
the request's ``use_llm`` is true). ``signals`` / ``extractive`` force the local path even
with a key. A provider that is unknown or unavailable falls back to the local default.

Adding a provider = one module with a class implementing the protocol from
:mod:`app.modules.ai.base`, plus one ``register_*`` call (e.g. at the bottom of that module,
imported from :data:`PLUGIN_MODULES` or the app start-up).
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import Settings
from app.modules.ai.base import HighlightRanker, MetadataProvider, TranscriptionProvider
from app.modules.ai.providers import (
    ClaudeMetadata,
    ClaudeRanker,
    ExtractiveMetadata,
    FasterWhisperProvider,
    SignalsRanker,
)

log = logging.getLogger(__name__)

PLUGIN_MODULES: tuple[str, ...] = ()        # extra provider modules to import (they call register_*)


class ProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Settings.model_config.get("env_file"), env_file_encoding="utf-8",
                                      extra="ignore")
    transcription_provider: str = "faster_whisper"
    highlight_ranker: str = "auto"
    metadata_provider: str = "auto"


_TRANSCRIPTION: dict[str, Callable[[], TranscriptionProvider]] = {"faster_whisper": FasterWhisperProvider}
_RANKERS: dict[str, Callable[[], HighlightRanker]] = {"signals": SignalsRanker, "claude": ClaudeRanker}
_METADATA: dict[str, Callable[[], MetadataProvider]] = {"extractive": ExtractiveMetadata, "claude": ClaudeMetadata}


def register_transcription_provider(name: str, factory: Callable[[], TranscriptionProvider]) -> None:
    _TRANSCRIPTION[name.lower()] = factory


def register_highlight_ranker(name: str, factory: Callable[[], HighlightRanker]) -> None:
    _RANKERS[name.lower()] = factory


def register_metadata_provider(name: str, factory: Callable[[], MetadataProvider]) -> None:
    _METADATA[name.lower()] = factory


@lru_cache
def provider_settings() -> ProviderSettings:
    for module in PLUGIN_MODULES:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - a broken plugin must not break the pipeline
            log.warning("Provider module %s could not be imported: %s", module, exc)
    return ProviderSettings()


def _pick(kind: str, table: dict[str, Callable[[], Any]], wanted: str, default: str) -> Any:
    name = (wanted or default).strip().lower()
    factory = table.get(name)
    if factory is None:
        log.warning("Unknown %s %r; using %r", kind, name, default)
        factory = table[default]
    return factory()


def _auto(name: str, llm_choice: str, local_choice: str) -> str:
    if (name or "auto").strip().lower() != "auto":
        return name
    from app.core.config import get_settings

    return llm_choice if get_settings().llm_configured else local_choice


def get_transcription_provider() -> TranscriptionProvider:
    provider = _pick("transcription provider", _TRANSCRIPTION, provider_settings().transcription_provider,
                     "faster_whisper")
    if provider.name != "faster_whisper" and not provider.available():
        log.warning("Transcription provider %s is unavailable; using faster_whisper", provider.name)
        return FasterWhisperProvider()
    return provider


def get_highlight_ranker() -> HighlightRanker:
    return _pick("highlight ranker", _RANKERS, _auto(provider_settings().highlight_ranker, "claude", "signals"),
                 "signals")


def get_metadata_provider() -> MetadataProvider:
    return _pick("metadata provider", _METADATA, _auto(provider_settings().metadata_provider, "claude", "extractive"),
                 "extractive")


def describe() -> dict[str, str]:
    """Names of the providers currently selected (for logs / capability endpoints)."""
    return {"transcription": get_transcription_provider().name, "highlight_ranker": get_highlight_ranker().name,
            "metadata": get_metadata_provider().name}
