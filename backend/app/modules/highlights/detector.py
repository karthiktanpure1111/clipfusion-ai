"""Highlight detection: candidate windows -> real signals -> transparent score -> refined,
de-duplicated clips (optionally re-ranked by Claude).

Pipeline (see :func:`detect_highlights`):

1. Sentence units from the word-level transcript; topic segments and discourse signals
   (:mod:`.context`).
2. Candidate windows per target duration ``D`` (targets and hard min/max lengths from the
   options, see :func:`app.modules.analysis.modes.clip_targets`): start at each sentence start
   (every ``window_step``-th in fast mode) and add whole sentences until the window is
   >= max(min_duration, 0.8 D) (never > min(max_duration, 1.25 D)), plus windows covering
   whole topic segments. Sources without (enough) speech get windows anchored on audio
   peaks, scene cuts and motion peaks plus a regular grid.
3. Raw signals per window (:mod:`.features`), normalised per video (:mod:`.scoring`).
4. Weighted 0..100 "AI highlight score (estimate)", category, timestamped reasons.
5. A diverse pool (``candidate_depth`` x max_clips; no temporal overlaps, no near-duplicate
   transcripts) is boundary-refined and RE-SCORED on the refined boundaries.
6. Optional ranking by the configured ranker (Claude when available), blended 0.55 LLM +
   0.45 signals, then the final diversity filter.
"""

from __future__ import annotations

import logging

import numpy as np

from app.modules.ai.registry import get_highlight_ranker
from app.modules.analysis.modes import clip_targets, profile_for
from app.modules.analysis.progress import ProgressFn, report
from app.modules.clipping.boundaries import refine_boundaries, temporal_iou
from app.modules.highlights import llm
from app.modules.highlights.dedupe import select_diverse
from app.modules.highlights.features import SignalContext, Window, raw_signals
from app.modules.highlights.scoring import categorise, normalise, reasons, score
from app.schemas.media import (
    AnalysisOptions,
    AudioAnalysis,
    HighlightCandidate,
    MediaInfo,
    SceneCut,
    TranscriptData,
    VisualActivity,
)

log = logging.getLogger(__name__)

MIN_TARGET_S, MAX_TARGET_S = 5.0, 600.0
LLM_WEIGHT, SIGNAL_WEIGHT = 0.55, 0.45
LLM_MATCH_IOU = 0.5
MAX_ANCHORS = 300
TRANSCRIPT_PREVIEW_CHARS = 4000


# ------------------------------------------------------------------ windows
def _bounds(target: float, limits: tuple[float, float]) -> tuple[float, float]:
    """Allowed window length range for ``target`` within the hard ``limits``."""
    lo, hi = max(limits[0], 0.8 * target), min(limits[1], 1.25 * target)
    return (lo, hi) if lo <= hi else (min(lo, hi), max(lo, hi))


def _speech_windows(ctx: SignalContext, target: float, limits: tuple[float, float] = (0.0, 1e9),
                    step: int = 1) -> list[Window]:
    ss = ctx.sentences
    lo, hi = _bounds(target, limits)
    out: list[Window] = []
    j = 0
    for i in range(0, len(ss), max(1, step)):
        j = max(j, i)
        while j < len(ss) and ss[j].end - ss[i].start < lo:
            j += 1
        if j >= len(ss):
            break
        if ss[j].end - ss[i].start <= hi:
            out.append(Window(ss[i].start, ss[j].end, target))
    return out


def _topic_windows(ctx: SignalContext, targets: list[float], limits: tuple[float, float]) -> list[Window]:
    """Windows covering one or more WHOLE topic segments (closest target wins)."""
    ss, bounds = ctx.sentences, ctx.context.segment_bounds
    out: list[Window] = []
    for a in range(len(bounds)):
        start = ss[bounds[a][0]].start
        for b in range(a, len(bounds)):
            dur = ss[bounds[b][1]].end - start
            if dur > max(_bounds(t, limits)[1] for t in targets):
                break
            fitting = [t for t in targets if _bounds(t, limits)[0] <= dur <= _bounds(t, limits)[1]]
            if fitting:
                out.append(Window(start, ss[bounds[b][1]].end, min(fitting, key=lambda t: abs(t - dur))))
    return out


def _spread(values: list[float], limit: int) -> list[float]:
    if len(values) <= limit:
        return values
    idx = np.linspace(0, len(values) - 1, limit).round().astype(int)
    return [values[i] for i in idx]


def _signal_windows(ctx: SignalContext, target: float) -> list[Window]:
    """Windows anchored on loudness peaks, scene cuts and motion peaks, plus a half-overlap grid."""
    length = min(target, ctx.duration)
    if length < 3.0:
        return []
    anchors = sorted(ctx.peaks, key=lambda t: -ctx.db_at(t))[:MAX_ANCHORS // 3]
    anchors += _spread(ctx.cuts, MAX_ANCHORS // 3)
    if ctx.motion.size > 2:
        m = ctx.motion
        local = [i for i in range(1, m.size - 1) if m[i] >= m[i - 1] and m[i] >= m[i + 1] and m[i] > 0]
        anchors += [i / ctx.motion_hz for i in sorted(local, key=lambda i: -m[i])[:MAX_ANCHORS // 3]]
    latest = max(0.0, ctx.duration - length)
    starts = {round(min(latest, max(0.0, a - 0.3 * length)) * 2) / 2 for a in anchors}
    starts.update(round(float(s) * 2) / 2 for s in np.arange(0.0, latest + 1e-6, max(1.0, length / 2)))
    starts.add(round(latest * 2) / 2)
    return [Window(s, min(ctx.duration, s + length), target) for s in sorted(starts)]


def generate_windows(ctx: SignalContext, targets: list[float], speech_mode: bool,
                     limits: tuple[float, float] = (0.0, 1e9), step: int = 1) -> list[Window]:
    """All candidate windows for the requested target durations (deduplicated)."""
    windows: list[Window] = []
    for target in targets:
        found = _speech_windows(ctx, target, limits, step) if speech_mode else []
        windows.extend(found or _signal_windows(ctx, target))
    if speech_mode and ctx.context.segment_bounds:
        windows.extend(_topic_windows(ctx, targets, limits))
    if not windows and ctx.sentences:  # very short source: one window over all speech
        start, end = ctx.sentences[0].start, max(s.end for s in ctx.sentences)
        if end - start >= 3.0:
            windows.append(Window(start, end, min(targets)))
    seen: set[tuple[float, float]] = set()
    unique = []
    for w in windows:
        key = (round(w.start, 2), round(w.end, 2))
        if key not in seen:
            seen.add(key)
            unique.append(w)
    return unique


def _targets(options: AnalysisOptions) -> list[float]:
    values = [min(MAX_TARGET_S, max(MIN_TARGET_S, t)) for t in clip_targets(options)]
    return sorted(set(values)) or [30.0]


def _limits(options: AnalysisOptions, duration: float) -> tuple[float, float]:
    lo, hi = sorted((float(options.min_duration), float(options.max_duration)))
    return min(lo, duration), max(min(lo, duration), min(hi, duration if duration > 0 else hi))


# ---------------------------------------------------------------- candidates
def _text_in(ctx: SignalContext, start: float, end: float) -> tuple[str, str | None]:
    rng = ctx.sentence_range(start, end)
    if rng is None:
        words = [w.word.strip() for w in ctx.transcript.words() if start <= w.start < end]
        return " ".join(words)[:TRANSCRIPT_PREVIEW_CHARS], None
    first, last = rng
    text = " ".join(s.text for s in ctx.sentences[first:last + 1])
    return text[:TRANSCRIPT_PREVIEW_CHARS], ctx.sentences[first].text[:200]


def _refine(ctx: SignalContext, w: Window, transcript: TranscriptData, audio: AudioAnalysis,
            limits: tuple[float, float]) -> Window:
    start, end = refine_boundaries(w.start, w.end, transcript=transcript, audio=audio,
                                   media_duration=ctx.duration, target=w.target,
                                   min_len=limits[0], max_len=min(limits[1], 1.25 * w.target))
    return Window(start, end, w.target)


def _rescore(ctx: SignalContext, windows: list[Window], population: list[dict[str, float]],
             speech_mode: bool) -> list[HighlightCandidate]:
    """Score refined ``windows`` (normalised against the full candidate population) and attach
    transcript preview, opening hook, reasons and real retention."""
    raws = [raw_signals(ctx, w) for w in windows]
    feats = normalise(ctx, population + raws, speech_mode=speech_mode)[len(population):]
    out = []
    for w, raw, f in zip(windows, raws, feats, strict=True):
        cand = _candidate(w, raw, f)
        text, hook = _text_in(ctx, w.start, w.end)
        retention = raw.get("retention")
        out.append(cand.model_copy(update={
            "transcript": text, "hook": hook, "reasons": reasons(ctx, w, cand.breakdown, f),
            "retention_score": round(retention, 4) if retention is not None else None,
        }))
    return out


def _candidate(w: Window, raw: dict[str, float], feats: dict[str, float]) -> HighlightCandidate:
    value, breakdown = score(feats)
    return HighlightCandidate(start=round(w.start, 3), end=round(w.end, 3), score=value,
                              category=categorise(raw, feats), breakdown=breakdown,
                              target_duration=w.target, method="signals")


def _blend(c: HighlightCandidate, m: llm.LLMMoment) -> HighlightCandidate:
    breakdown = {k: round(SIGNAL_WEIGHT * v, 2) for k, v in c.breakdown.items()}
    breakdown["llm"] = round(LLM_WEIGHT * m.score, 2)
    reason = f"AI review: {m.reason.strip()}" if m.reason.strip() else None
    return c.model_copy(update={
        "score": round(min(100.0, LLM_WEIGHT * m.score + SIGNAL_WEIGHT * c.score), 2),
        "breakdown": breakdown, "category": m.category, "hook": (m.hook.strip() or c.hook),
        "reasons": ([reason] if reason else []) + c.reasons[:2], "method": "signals+llm",
    })


def _merge_llm(ctx: SignalContext, pool: list[HighlightCandidate], moments: list[llm.LLMMoment],
               raws: list[dict[str, float]], speech_mode: bool, targets: list[float],
               transcript: TranscriptData, audio: AudioAnalysis,
               limits: tuple[float, float]) -> list[HighlightCandidate]:
    merged: list[HighlightCandidate] = []
    used: set[int] = set()
    for c in pool:
        best, best_iou = None, LLM_MATCH_IOU
        for j, m in enumerate(moments):
            iou = temporal_iou(c.start, c.end, m.start, m.end)
            if j not in used and iou > best_iou:
                best, best_iou = j, iou
        if best is None:
            merged.append(c)
        else:
            used.add(best)
            merged.append(_blend(c, moments[best]))
    new_windows, new_moments = [], []
    for j, m in enumerate(moments):
        if j in used:
            continue
        target = min(targets, key=lambda t: abs(t - (m.end - m.start)))
        new_windows.append(_refine(ctx, Window(m.start, m.end, target), transcript, audio, limits))
        new_moments.append(m)
    if new_windows:
        for base, m in zip(_rescore(ctx, new_windows, raws, speech_mode), new_moments, strict=True):
            merged.append(_blend(base, m))
    return merged


# --------------------------------------------------------------------- public
def detect_highlights(*, transcript: TranscriptData, audio: AudioAnalysis, scenes: list[SceneCut],
                      visual: VisualActivity, media: MediaInfo, options: AnalysisOptions,
                      source_title: str | None = None,
                      progress: ProgressFn | None = None) -> list[HighlightCandidate]:
    """Find the best clip candidates of a source (sorted by score, at most ``options.max_clips``).

    Scores are "AI highlight score (estimate)" values, never viewership; ``retention_score``
    is set only from real YouTube Analytics data passed in ``options.retention``."""
    ctx = SignalContext(transcript=transcript, audio=audio, scenes=scenes, visual=visual, media=media,
                        retention=options.retention)
    if ctx.duration <= 0:
        return []
    profile = profile_for(options)
    targets = _targets(options)
    limits = _limits(options, ctx.duration)
    speech_mode = ctx.speech_mode()
    windows = generate_windows(ctx, targets, speech_mode, limits, profile.window_step)
    log.info("highlight detection: %d windows (%s mode, targets %s, limits %s, %d topic segments)", len(windows),
             "speech" if speech_mode else "signal", targets, limits, len(ctx.context.segment_bounds))
    if not windows:
        return []
    report(progress, 0.05, "Scoring candidate moments")
    raws: list[dict[str, float]] = []
    for i, w in enumerate(windows):
        raws.append(raw_signals(ctx, w))
        if i % 500 == 499:
            report(progress, 0.05 + 0.5 * (i + 1) / len(windows), None)
    feats = normalise(ctx, raws, speech_mode=speech_mode)
    cands = [_candidate(w, r, f) for w, r, f in zip(windows, raws, feats, strict=True)]
    position = {id(c): i for i, c in enumerate(cands)}
    depth = max(profile.candidate_depth * options.max_clips, 20)
    pool = select_diverse(cands, options.max_overlap, limit=depth,
                          text_of=lambda c: _text_in(ctx, c.start, c.end)[0])
    report(progress, 0.6, "Refining clip boundaries")
    refined = [_refine(ctx, windows[position[id(c)]], transcript, audio, limits) for c in pool]
    pool = _rescore(ctx, refined, raws, speech_mode)
    ranker = get_highlight_ranker()
    if speech_mode and ranker.active(options.use_llm):
        report(progress, 0.7, f"Ranking moments ({ranker.name})")
        moments = ranker.rank(sentences=ctx.sentences, candidates=pool, duration=ctx.duration, targets=targets,
                              max_clips=options.max_clips, source_title=source_title)
        if moments:
            pool = _merge_llm(ctx, pool, moments, raws, speech_mode, targets, transcript, audio, limits)
    final = [c for c in select_diverse(pool, options.max_overlap) if c.score >= options.min_score]
    report(progress, 1.0, f"{min(len(final), options.max_clips)} highlights")
    return final[: options.max_clips]
