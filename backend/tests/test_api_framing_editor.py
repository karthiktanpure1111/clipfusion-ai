"""Framing editor API on a real analysed video (real face tracking, real protection engine):
GET/PUT/check/suggest/acknowledge, render gating (409 until acknowledged), apply + presets."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import GeneratedClip
from app.modules.framing import service as framing_service
from app.modules.framing.geometry import visible_source_rect_px
from app.modules.framing.layout import compile_layout
from app.modules.framing.suggest import suggest_framing
from app.schemas.framing import FramingConfig, blocking_warnings
from tests.helpers.vision_media import analysed, face_photo
from tests.test_api_clips_factories import (
    api_client,
    headers_for,
    make_clip,
    make_source,
    record_enqueue,
)

API = "/api/v1"
LEFT_CROP = {"x": 0.0, "y": 0.0, "w": 0.3164, "h": 1.0}


@pytest.fixture()
def enqueued(monkeypatch) -> list[str]:
    return record_enqueue(monkeypatch)


@pytest.fixture(scope="module")
def a1b_client():
    return api_client()


@pytest.fixture(scope="module")
def podcast(tmp_media_dir: Path):
    if face_photo() is None:
        pytest.skip("test portrait unavailable (offline)")
    d = tmp_media_dir / "a1b-framing"
    d.mkdir(parents=True, exist_ok=True)
    return analysed("podcast", d)


def _analysed_clip(db, user, podcast) -> GeneratedClip:
    path, media, vision = podcast
    src = make_source(db, user, video=path, media=media)
    clip = make_clip(db, user, src, start=0.0, end=round(media.duration, 3))
    framing_service.save_framing(db, clip, suggest_framing(vision, media, clip.duration), media=media,
                                 vision=vision, auto_generated=True)
    db.commit()
    return clip


def _manual(crop: dict) -> dict:
    return {"mode": "manual", "manual": {"crop": crop, "fit": "cover", "lock_aspect": True}}


def test_get_without_vision_returns_full_frame(db, user, other_user, a1b_client) -> None:
    clip = make_clip(db, user, make_source(db, user))
    r = a1b_client.get(f"{API}/clips/{clip.id}/framing", headers=headers_for(user))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "full_frame" and body["vision"] is None and body["auto_generated"] is True
    assert [w["code"] for w in body["warnings"]] == ["vision_missing"] and body["warnings"][0]["severity"] == "info"
    assert body["clip_duration"] == 30.0 and body["source"]["width"] == 1920 and body["compiled"]["layers"]
    assert a1b_client.get(f"{API}/clips/{clip.id}/framing", headers=headers_for(other_user)).status_code == 404


def test_put_check_suggest_and_render_gating(db, user, a1b_client, enqueued, podcast) -> None:
    clip = _analysed_clip(db, user, podcast)
    h = headers_for(user)
    body = a1b_client.get(f"{API}/clips/{clip.id}/framing", headers=h).json()
    assert body["vision"]["tracks"] and body["proxy_url"] is None
    assert not blocking_warnings(framing_service.stored_warnings(clip))  # the suggestion is safe
    if body["mode"] == "smart":
        assert len(body["compiled"]["layers"][0]["keyframes"]) >= 2  # follows the moving person
    # dry run of a crop that loses the moving person: warnings, nothing saved
    check = a1b_client.post(f"{API}/clips/{clip.id}/framing/check", json={"config": _manual(LEFT_CROP)},
                            headers=h).json()
    codes = {w["code"] for w in check["warnings"] if w["severity"] != "info"}
    assert codes & {"face_outside", "head_cut", "streamer_hidden"}, check["warnings"]
    assert any(s["config_patch"] for w in check["warnings"] for s in w["suggestions"])
    assert a1b_client.get(f"{API}/clips/{clip.id}/framing", headers=h).json()["mode"] == body["mode"]
    # save it: user-edited, warnings stored, render refused until acknowledged
    put = a1b_client.put(f"{API}/clips/{clip.id}/framing", json={"config": _manual(LEFT_CROP)}, headers=h).json()
    assert put["mode"] == "manual" and put["auto_generated"] is False and put["warnings_acknowledged"] is False
    detail = a1b_client.get(f"{API}/clips/{clip.id}", headers=h).json()
    assert detail["blocking_warning_count"] >= 1 and detail["framing_mode"] == "manual"
    r = a1b_client.post(f"{API}/clips/{clip.id}/render", json={"acknowledge_warnings": False}, headers=h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "framing_warnings_unacknowledged"
    assert {w["code"] for w in r.json()["error"]["details"]} == codes
    assert enqueued == []
    r = a1b_client.post(f"{API}/clips/{clip.id}/render", json={"acknowledge_warnings": True}, headers=h)
    assert r.status_code == 200 and r.json()["job_type"] == "render" and enqueued == ["render_clip"]
    again = a1b_client.post(f"{API}/clips/{clip.id}/render", json={}, headers=h).json()
    assert again["id"] == r.json()["id"] and enqueued == ["render_clip"]  # idempotent while active
    state = a1b_client.get(f"{API}/clips/{clip.id}/render", headers=h).json()
    assert state["render_status"] == "queued" and state["job"]["id"] == r.json()["id"]
    assert a1b_client.get(f"{API}/clips/{clip.id}/framing", headers=h).json()["warnings_acknowledged"] is True
    # suggestion (not saved) is safe again
    sug = a1b_client.post(f"{API}/clips/{clip.id}/framing/suggest", headers=h).json()
    assert not [w for w in sug["warnings"] if w["severity"] != "info"]
    assert a1b_client.get(f"{API}/clips/{clip.id}/framing", headers=h).json()["mode"] == "manual"


def test_acknowledge_resets_when_blocking_warnings_change(db, user, a1b_client, enqueued, podcast) -> None:
    clip = _analysed_clip(db, user, podcast)
    h = headers_for(user)
    a1b_client.put(f"{API}/clips/{clip.id}/framing", json={"config": _manual(LEFT_CROP)}, headers=h)
    ack = a1b_client.post(f"{API}/clips/{clip.id}/framing/acknowledge", headers=h).json()
    assert ack["warnings_acknowledged"] is True
    # same crop (same blocking warnings) keeps the acknowledgement
    same = a1b_client.put(f"{API}/clips/{clip.id}/framing", json={"config": _manual(LEFT_CROP)}, headers=h).json()
    assert same["warnings_acknowledged"] is True
    # full frame has no blocking warnings -> the set changed -> acknowledgement reset
    ff = a1b_client.put(f"{API}/clips/{clip.id}/framing", json={"config": {"mode": "full_frame"}}, headers=h).json()
    assert ff["warnings_acknowledged"] is False and not [w for w in ff["warnings"] if w["severity"] != "info"]
    # smart keyframes sent by the client are ignored and recomputed from the stored vision
    smart = {"mode": "smart", "smart": {"keyframes": [{"t": 0, "rect": {"x": 0.9, "y": 0, "w": 0.1, "h": 0.1}}]}}
    out = a1b_client.put(f"{API}/clips/{clip.id}/framing", json={"config": smart}, headers=h).json()
    keys = out["config"]["smart"]["keyframes"]
    assert keys and all(abs(k["rect"]["h"] - 1.0) < 1e-6 for k in keys)
    r = a1b_client.post(f"{API}/clips/{clip.id}/framing/analyze", headers=h)
    assert r.status_code == 200 and r.json()["job_type"] == "clip_vision" and enqueued == ["clip_vision"]


def test_presets_and_apply(db, user, other_user, a1b_client, enqueued, podcast) -> None:
    clip = _analysed_clip(db, user, podcast)
    square = make_clip(db, user, make_source(db, user, width=1440, height=1080))
    foreign = make_clip(db, other_user, make_source(db, other_user))
    h = headers_for(user)
    cfg = _manual({"x": 0.3, "y": 0.0, "w": 0.3164, "h": 1.0})
    cfg["manual"]["keyframes"] = [{"t": 1.0, "rect": {"x": 0.1, "y": 0, "w": 0.3164, "h": 1}}]
    r = a1b_client.post(f"{API}/framing-presets", json={"name": "Centre", "config": cfg, "is_default": True},
                        headers=h)
    assert r.status_code == 201, r.text
    preset = r.json()
    assert preset["is_default"] and preset["config"]["manual"]["keyframes"] == [] and preset["mode"] == "manual"
    dup = a1b_client.post(f"{API}/framing-presets", json={"name": "Centre", "config": cfg}, headers=h)
    assert dup.status_code == 409
    second = a1b_client.post(f"{API}/framing-presets", json={"name": "Full", "config": {"mode": "full_frame"},
                                                              "is_default": True}, headers=h).json()
    listed = {p["name"]: p for p in a1b_client.get(f"{API}/framing-presets", headers=h).json()}
    assert listed["Full"]["is_default"] and not listed["Centre"]["is_default"]
    db.refresh(user)
    assert user.settings["default_framing_preset_id"] == second["id"]
    r = a1b_client.post(f"{API}/framing/apply", json={"clip_ids": [str(clip.id), str(square.id), str(foreign.id)],
                                                      "preset_id": preset["id"]}, headers=h)
    results = {x["clip_id"]: x for x in r.json()["results"]}
    assert results[str(clip.id)]["ok"] and results[str(square.id)]["ok"]
    assert results[str(foreign.id)]["error"]["code"] == "not_found"
    assert any(w["severity"] != "info" for w in results[str(clip.id)]["warnings"])  # centre crop misses the mover
    sq = a1b_client.get(f"{API}/clips/{square.id}/framing", headers=h).json()
    crop = sq["config"]["manual"]["crop"]
    assert abs((crop["w"] * 1440) / (crop["h"] * 1080) - 9 / 16) < 0.01  # re-fitted to the 4:3 source
    assert abs(crop["x"] + crop["w"] / 2 - (0.3 + 0.3164 / 2)) < 0.01 and sq["auto_generated"] is False
    patched = a1b_client.patch(f"{API}/framing-presets/{preset['id']}", json={"name": "Centred"}, headers=h).json()
    assert patched["name"] == "Centred"
    assert a1b_client.patch(f"{API}/framing-presets/{preset['id']}", json={"name": "x"},
                            headers=headers_for(other_user)).status_code == 404
    assert a1b_client.delete(f"{API}/framing-presets/{second['id']}", headers=h).status_code == 204
    db.refresh(user)
    assert user.settings["default_framing_preset_id"] is None
    r = a1b_client.post(f"{API}/framing/apply", json={"clip_ids": [str(square.id)], "config": {"mode": "pip"}},
                        headers=h)
    assert r.json()["results"][0]["ok"]
    assert a1b_client.post(f"{API}/framing/apply", json={"clip_ids": [str(square.id)]}, headers=h).status_code == 422


def test_trim_invalidates_the_old_face_analysis(db, user, a1b_client, enqueued, podcast) -> None:
    """Face positions are relative to the analysed start, so they must not be reused after a trim;
    an animated manual crop stays aligned with the picture and the acknowledgement is reset."""
    clip = _analysed_clip(db, user, podcast)
    h = headers_for(user)
    cfg = _manual(LEFT_CROP)
    cfg["manual"]["keyframes"] = [{"t": 0, "rect": {"x": 0.0, "y": 0, "w": 0.3, "h": 1}},
                                  {"t": 8, "rect": {"x": 0.4, "y": 0, "w": 0.3, "h": 1}}]
    a1b_client.put(f"{API}/clips/{clip.id}/framing", json={"config": cfg}, headers=h)
    assert a1b_client.post(f"{API}/clips/{clip.id}/framing/acknowledge", headers=h).json()["warnings_acknowledged"]
    r = a1b_client.patch(f"{API}/clips/{clip.id}", json={"start_time": 2.0}, headers=h)
    assert r.status_code == 200 and enqueued == ["clip_vision"]
    out = a1b_client.get(f"{API}/clips/{clip.id}/framing", headers=h).json()
    assert out["vision"] is None and out["warnings_acknowledged"] is False
    assert "vision_missing" in {w["code"] for w in out["warnings"]}
    keys = out["config"]["manual"]["keyframes"]
    assert [k["t"] for k in keys] == [0.0, 6.0]
    assert abs(keys[0]["rect"]["x"] - 0.1) < 1e-6 and abs(keys[1]["rect"]["x"] - 0.4) < 1e-6


@pytest.mark.parametrize("rect", [{"x": 0.05, "y": 0.1, "w": 0.2, "h": 0.7}, {"x": 0.1, "y": 0.3, "w": 0.8, "h": 0.2}])
def test_refit_shows_exactly_what_was_previewed(rect) -> None:
    """Tall and wide ``cover`` panels: re-fitting never changes the visible source region."""
    config = FramingConfig.model_validate({"mode": "facecam_gameplay", "facecam_gameplay": {
        "facecam": rect, "gameplay": rect, "split": 0.35}})
    refitted = framing_service.refit_config(config, 1920, 1080)
    for before, after in zip(compile_layout(config, 1920, 1080).layers, compile_layout(refitted, 1920, 1080).layers,
                             strict=True):
        a, b = (visible_source_rect_px(layer, 0.0, 1920, 1080, 1080, 1920) for layer in (before, after))
        assert all(abs(getattr(a, k) - getattr(b, k)) < 0.002 for k in "xywh"), (a, b)
