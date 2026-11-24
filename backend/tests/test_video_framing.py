"""Per-video framing: the framing saved on one clip is applied to the other clips of the same video."""

from __future__ import annotations

from app.models import GeneratedClip, VideoSource
from app.modules.framing import service as framing
from app.modules.framing.layout import default_framing_config
from app.modules.rendering.service import compute_render_hash
from tests.test_upload_runs_factories import API, headers, make_world, tasks  # noqa: F401 - fixture


def _with_framing(db, clips) -> None:  # noqa: ANN001
    for clip in clips:
        framing.save_framing(db, clip, default_framing_config(1920, 1080), auto_generated=True)
        db.commit()
        clip.render_hash = compute_render_hash(clip)  # rendered with this framing
        db.commit()


def test_saved_framing_applies_to_the_whole_video(client, db, user, tasks) -> None:  # noqa: F811
    _acc, src, clips = make_world(db, user, [10.0, 60.0, 120.0, 180.0])
    _other_acc, other_src, other = make_world(db, user, [10.0])
    _with_framing(db, clips + other)
    clips[2].upload_status = "uploaded"  # already on YouTube: left alone
    db.commit()
    manual = default_framing_config(1920, 1080, "manual").model_dump(mode="json")

    # without apply_to_video only the edited clip changes
    r = client.put(f"{API}/clips/{clips[0].id}/framing", json={"config": manual}, headers=headers(user))
    assert r.status_code == 200 and r.json()["applied_to_clips"] == 0
    db.expire_all()
    assert db.get(GeneratedClip, clips[1].id).framing.mode == "full_frame"

    r = client.put(f"{API}/clips/{clips[0].id}/framing", params={"apply_to_video": "true"}, json={"config": manual},
                   headers=headers(user))
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "manual" and r.json()["applied_to_clips"] == 2  # clips 2 and 4
    db.expire_all()
    modes = {c.id: db.get(GeneratedClip, c.id).framing.mode for c in clips}
    assert modes == {clips[0].id: "manual", clips[1].id: "manual", clips[2].id: "full_frame", clips[3].id: "manual"}
    assert db.get(GeneratedClip, clips[1].id).framing.auto_generated is True
    assert db.get(GeneratedClip, other[0].id).framing.mode == "full_frame"  # other videos untouched
    assert db.get(VideoSource, src.id).framing_template["mode"] == "manual"
    assert db.get(VideoSource, other_src.id).framing_template is None
    assert not any(name == "render_clip" for name, _ in tasks)  # nothing renders by itself: the user renders
    from app.modules.rendering.service import render_is_current

    assert not render_is_current(db.get(GeneratedClip, clips[1].id))  # its old render is out of date now
    # new clips of the video start with the video's framing
    assert framing.video_template(db, db.get(GeneratedClip, clips[1].id)).mode == "manual"
