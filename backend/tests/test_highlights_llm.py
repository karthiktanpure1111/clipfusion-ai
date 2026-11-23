"""Claude ranking of highlights with a fake Anthropic client (the real API is never called)."""

from __future__ import annotations

import json
import logging
import re
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import get_settings
from app.modules.highlights import llm_client
from app.modules.highlights.detector import detect_highlights
from app.schemas.media import AnalysisOptions
from tests.test_highlights_detector import signals  # noqa: F401 - shared module fixture


# ----------------------------------------------------------------- fake client
class FakeStream:
    def __init__(self, message: Any) -> None:
        self.message = message

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def get_final_message(self) -> Any:
        return self.message


class FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        response = self.responses[min(len(self.calls), len(self.responses)) - 1]
        if isinstance(response, BaseException):
            raise response
        return FakeStream(response)


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.beta = SimpleNamespace(messages=FakeMessages(responses))

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.beta.messages.calls


def fake_message(payload: Any, stop_reason: str = "end_turn") -> SimpleNamespace:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(stop_reason=stop_reason, stop_details=None,
                           content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)])


@pytest.fixture()
def llm_on(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test-not-real")
    monkeypatch.setattr(settings, "llm_enabled", True)

    def install(responses: list[Any]) -> FakeClient:
        client = FakeClient(responses)
        monkeypatch.setattr(llm_client, "get_llm_client", lambda: client)
        return client

    return install


MOMENTS = {"moments": [
    {"start": 120.0, "end": 135.0, "category": "funny", "score": 95,
     "reason": "The streamer lands a clutch play and bursts out laughing.",
     "hook": "No way, that was the craziest clutch I have ever seen!"},
    {"start": 30.0, "end": 50.0, "category": "explanation", "score": 70,
     "reason": "A calm walkthrough of the weekly update.", "hook": "We are talking about the weekly update."},
]}


def _options(**kw: Any) -> AnalysisOptions:
    return AnalysisOptions(**{"clip_durations": [15.0], "min_duration": 10.0, "max_clips": 6, "use_llm": True, **kw})


def test_llm_ranking_merges_with_signals(llm_on, signals) -> None:  # noqa: F811
    client = llm_on([fake_message(MOMENTS)])
    cands = detect_highlights(**signals, options=_options(), source_title="Friday stream")
    assert len(client.calls) == 1
    call = client.calls[0]
    settings = get_settings()
    assert call["model"] == settings.llm_model and call["max_tokens"] == 32000
    assert call["betas"] == ["server-side-fallback-2026-07-01"] and call["fallbacks"] == "default"
    assert call["output_config"]["effort"] == settings.llm_effort
    assert call["output_config"]["format"]["type"] == "json_schema"
    schema = call["output_config"]["format"]["schema"]
    item = schema["properties"]["moments"]["items"]
    assert item["additionalProperties"] is False and set(item["required"]) == set(item["properties"])
    assert not {"thinking", "temperature", "top_p", "top_k"} & set(call)
    assert call["messages"][-1]["role"] == "user" and len(call["messages"]) == 1
    assert re.search(r"\[2:0\d\] Wait, what just happened\?", call["messages"][0]["content"])
    assert "Friday stream" in call["messages"][0]["content"]

    merged = [c for c in cands if c.method == "signals+llm"]
    assert len(merged) >= 2
    top = next(c for c in merged if c.start < 135 and c.end > 120)
    assert top.category == "funny" and "llm" in top.breakdown
    assert top.reasons[0].startswith("AI review: The streamer lands a clutch")
    assert top.hook == "No way, that was the craziest clutch I have ever seen!"
    llm_only = next(c for c in merged if c.end <= 60)
    words = signals["transcript"].words()
    for t in (llm_only.start, llm_only.end):
        assert not any(w.start + 1e-3 < t < w.end - 1e-3 for w in words)
    assert llm_only.end - llm_only.start <= 1.25 * 15.0 + 0.01


@pytest.mark.parametrize("response", [
    fake_message({"moments": []}, stop_reason="refusal"),
    fake_message('{"moments": [', stop_reason="max_tokens"),
    fake_message("this is not json"),
    fake_message({"moments": [{"start": "soon"}]}),
    RuntimeError("socket closed"),
])
def test_llm_failures_fall_back_to_signals(llm_on, signals, caplog, response) -> None:  # noqa: F811
    llm_on([response])
    with caplog.at_level(logging.WARNING, logger="app.modules.highlights.llm_client"):
        cands = detect_highlights(**signals, options=_options())
    assert cands and all(c.method == "signals" for c in cands)
    assert all("llm" not in c.breakdown for c in cands)
    assert any("non-LLM result" in r.getMessage() for r in caplog.records)


def test_api_connection_error_falls_back(llm_on, signals) -> None:  # noqa: F811
    import anthropic
    import httpx2

    llm_on([anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))])
    cands = detect_highlights(**signals, options=_options())
    assert cands and all(c.method == "signals" for c in cands)


def test_llm_not_used_when_disabled_or_unconfigured(llm_on, signals, monkeypatch) -> None:  # noqa: F811
    client = llm_on([fake_message(MOMENTS)])
    cands = detect_highlights(**signals, options=_options(use_llm=False))
    assert not client.calls and all(c.method == "signals" for c in cands)
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "")
    cands = detect_highlights(**signals, options=_options())
    assert not client.calls and all(c.method == "signals" for c in cands)


def test_long_transcripts_are_chunked(llm_on, signals, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(get_settings(), "llm_max_transcript_chars", 2000)
    client = llm_on([fake_message({"moments": []})])
    detect_highlights(**signals, options=_options())
    assert len(client.calls) >= 2
    prompts = [c["messages"][0]["content"] for c in client.calls]
    assert all("transcript part" in p for p in prompts)
    assert all(len(p.split("Transcript (one line per sentence")[1]) <= 2100 for p in prompts)
