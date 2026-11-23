"""Automatic framing suggestion. Deterministic; never crops a face: every candidate is checked
with :func:`analyze_protection` and the first one without blocking warnings wins — otherwise
the safe ``full_frame`` layout is returned.

Candidate order:
1. a facecam (streamer) -> stacked facecam + gameplay (gameplay cropped around the activity,
   then the whole gameplay contained in its panel);
2. one dominant person -> AI smart crop following them;
3. several people -> smart crop with speaker auto-switch when the speakers are
   distinguishable; two people far apart -> the stacked two-person layout;
4. full_frame.
"""

from __future__ import annotations

import logging

from app.modules.framing.layout import default_framing_config
from app.modules.framing.presets import facecam_gameplay_config, two_person_config
from app.modules.framing.protection import analyze_protection, prepare_config
from app.modules.framing.tracks import significant_tracks
from app.schemas.framing import (
    ClipVisionAnalysis,
    FaceTrack,
    FramingConfig,
    SmartCropConfig,
    blocking_warnings,
)
from app.schemas.media import MediaInfo

log = logging.getLogger(__name__)

_FAR_APART = 0.35  # normalised horizontal distance between two people for the stacked layout


def _smart(base: FramingConfig, track: FaceTrack, *, auto_switch: bool) -> FramingConfig:
    smart = SmartCropConfig(primary_track_id=None if auto_switch else track.id, auto_switch=auto_switch)
    return base.model_copy(update={"mode": "smart", "smart": smart})


def _distinguishable(vision: ClipVisionAnalysis, people: list[FaceTrack], min_shot: float) -> bool:
    ids = {p.id for p in people}
    long_enough = {s.track_id for s in vision.speaker_timeline
                   if s.track_id in ids and s.end - s.start >= min_shot}
    return len(long_enough) >= 2


def _candidates(vision: ClipVisionAnalysis, base: FramingConfig, sw: int, sh: int) -> list[FramingConfig]:
    sig = significant_tracks(vision)
    facecams = sorted((t for t in sig if t.is_facecam), key=lambda t: (-t.prominence, t.id))
    people = sorted((t for t in sig if not t.is_facecam), key=lambda t: (-t.prominence, t.id))
    out: list[FramingConfig] = []
    if facecams:
        out.append(facecam_gameplay_config(base, vision, facecams[0], sw, sh))
        out.append(facecam_gameplay_config(base, vision, facecams[0], sw, sh, contain_gameplay=True))
    if len(people) == 1:
        out.append(_smart(base, people[0], auto_switch=False))
    elif len(people) >= 2:
        if _distinguishable(vision, people, SmartCropConfig().min_shot_seconds):
            out.append(_smart(base, people[0], auto_switch=True))
        if len(people) == 2 and abs(people[0].mean_center[0] - people[1].mean_center[0]) >= _FAR_APART:
            stacked = two_person_config(base, people[0], people[1], sw, sh)
            if stacked is not None:
                out.append(stacked)
    return out


def suggest_framing(vision: ClipVisionAnalysis | None, media: MediaInfo, duration: float) -> FramingConfig:
    """Best safe framing for a clip (smart configs come with computed keyframes)."""
    sw, sh = media.width, media.height
    base = default_framing_config(sw, sh, "full_frame")
    if vision is None or not vision.tracks or sw / sh <= (base.output_width / base.output_height) * 1.01:
        return base
    for cand in _candidates(vision, base, sw, sh):
        cfg = prepare_config(cand, vision, sw, sh, duration)
        warnings = analyze_protection(cfg, vision, source_w=sw, source_h=sh, duration=duration)
        blocking = blocking_warnings(warnings)
        if not blocking:
            return cfg
        log.debug("framing candidate %s rejected: %s", cfg.mode, [w.code for w in blocking])
    return base
