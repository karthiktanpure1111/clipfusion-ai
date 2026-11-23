"""Per-video feature normalisation, weighted scoring, categories and human-readable reasons.

Intensity features (audio energy, lexical cue density, speech rate, visual activity) are
mapped to 0..1 *relative to the other windows of the same video* (robust percentiles).
Structure features from :mod:`.context` (topic completeness, standalone opening, ending
quality, story / question-answer arc) and ``completeness`` are already absolute 0..1
judgements and are used as they are. The weighted sum is the 0..100
"AI highlight score (estimate)"; ``breakdown`` holds each feature's contribution on the same
0..100 scale (the contributions sum to the score). Weights of absent features (no speech,
no video, no retention data) are redistributed over the present ones.
"""

from __future__ import annotations

import math

import numpy as np

from app.modules.analysis.timecode import format_timestamp
from app.modules.highlights.features import LEXICAL_GROUPS, SignalContext, Window, raw_signals
from app.modules.highlights.lexicon import GROUP_LABELS

FEATURE_WEIGHTS: dict[str, float] = {
    "audio_energy": 0.13,  # loud / excited delivery relative to the rest of the video
    "speech": 0.09,        # dense speech, little dead air
    "lexical": 0.16,       # reaction / statement / story / explanation cues
    "hook": 0.12,          # strength of the opening line
    "visual": 0.08,        # scene changes + on-screen motion
    "completeness": 0.08,  # starts / ends on whole sentences
    "topic": 0.12,         # covers complete topic segment(s), starts and ends on topic boundaries
    "standalone": 0.08,    # the opening does not depend on earlier context
    "ending": 0.09,        # ends on a conclusion / punchline, not mid-thought
    "arc": 0.05,           # story arc or a question answered inside the clip
    "retention": 0.12,     # only when real YouTube retention data is supplied
}
STRUCTURE_FEATURES = ("topic", "standalone", "ending", "arc")
DEAD_AIR_START_S, DEAD_AIR_SPAN_S = 1.5, 3.0
LEX_WEIGHTS: dict[str, float] = {
    "laughter": 1.5, "surprise": 1.2, "strong_statement": 1.0, "emotional": 1.0, "explanation": 0.6,
    "gaming": 1.2, "audience": 0.8,
}
EXCLAM_WEIGHT, QA_WEIGHT, NUMBER_WEIGHT = 0.6, 1.5, 0.3
CATEGORY_MIN_DENSITY = 0.6     # category cue strength (per 30 s) needed to beat "exciting"/"other"


def robust_scale(values: np.ndarray) -> np.ndarray:
    """Map values to 0..1 between the 10th and 90th percentile of the population."""
    if values.size == 0:
        return values
    lo, hi = np.percentile(values, 10), np.percentile(values, 90)
    if hi - lo < 1e-9:
        return np.where(values > hi, 1.0, 0.5 if hi > 0 else 0.0).astype(np.float64)
    return np.clip((values - lo) / (hi - lo), 0.0, 1.0)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _col(raws: list[dict[str, float]], key: str, default: float = 0.0) -> np.ndarray:
    return np.array([r.get(key, default) for r in raws], dtype=np.float64)


def lexical_density(r: dict[str, float]) -> float:
    """Weighted lexical cue count per 30 s of the window."""
    total = sum(LEX_WEIGHTS[g] * r.get(f"lex_{g}", 0.0) for g in LEXICAL_GROUPS)
    total += EXCLAM_WEIGHT * r.get("exclam", 0.0) + QA_WEIGHT * r.get("qa", 0.0) + NUMBER_WEIGHT * r.get("numbers", 0.0)
    return total * 30.0 / max(r["duration"], 1.0)


def normalise(ctx: SignalContext, raws: list[dict[str, float]], *, speech_mode: bool) -> list[dict[str, float]]:
    """Turn raw window signals into 0..1 features (keys of :data:`FEATURE_WEIGHTS`)."""
    n = len(raws)
    feats: list[dict[str, float]] = [{} for _ in range(n)]
    if n == 0:
        return feats
    dur = np.maximum(_col(raws, "duration"), 1.0)
    cols: dict[str, np.ndarray] = {}
    if ctx.has_audio:
        loud = _col(raws, "loud_top20")
        scale = max((np.percentile(loud, 75) - np.percentile(loud, 25)) / 1.349, 1.0)
        z = (loud - np.median(loud)) / scale
        peak_rate = np.minimum(1.0, _col(raws, "peaks") / np.maximum(1.0, dur / 20.0))
        cols["audio_energy"] = 0.75 * _sigmoid(1.2 * z) + 0.25 * peak_rate
    if speech_mode:
        wps = robust_scale(_col(raws, "words_per_s"))
        speech = 0.5 * wps + 0.5 * _col(raws, "speech_frac") - 0.6 * _col(raws, "silence_frac")
        dead_air = np.clip((_col(raws, "max_gap") - DEAD_AIR_START_S) / DEAD_AIR_SPAN_S, 0.0, 1.0)
        cols["speech"] = np.clip(speech - 0.25 * dead_air, 0.0, 1.0)
        cols["lexical"] = robust_scale(np.array([lexical_density(r) for r in raws]))
        cols["hook"] = _col(raws, "hook")
        for key in STRUCTURE_FEATURES:
            cols[key] = np.clip(_col(raws, key), 0.0, 1.0)
    motion_norm = robust_scale(_col(raws, "motion")) if ctx.motion.size else np.zeros(n)
    if ctx.has_visual:
        cut_density = robust_scale(_col(raws, "cuts") * 30.0 / dur)
        cols["visual"] = 0.5 * cut_density + 0.5 * motion_norm if ctx.motion.size else cut_density
    cols["completeness"] = _col(raws, "completeness")
    if ctx.retention:
        cols["retention"] = robust_scale(_col(raws, "retention"))
    for i in range(n):
        feats[i] = {k: float(v[i]) for k, v in cols.items()}
        feats[i]["_motion_norm"] = float(motion_norm[i])
    return feats


def score(feats: dict[str, float]) -> tuple[float, dict[str, float]]:
    """Weighted 0..100 score and per-feature contributions over the features present."""
    active = {k: w for k, w in FEATURE_WEIGHTS.items() if k in feats}
    total_w = sum(active.values()) or 1.0
    breakdown = {k: round(100.0 * w * feats[k] / total_w, 2) for k, w in active.items()}
    return round(min(100.0, max(0.0, sum(breakdown.values()))), 2), breakdown


def categorise(r: dict[str, float], feats: dict[str, float]) -> str:
    """Best-matching :data:`HighlightCategory` from the cue densities and signal features."""
    per30 = 30.0 / max(r["duration"], 1.0)
    lex = {g: r.get(f"lex_{g}", 0.0) * per30 for g in LEXICAL_GROUPS}
    energy = feats.get("audio_energy", 0.0)
    reactive = lex["surprise"] + lex["laughter"] + r.get("exclam", 0.0) * per30
    scores = {
        "funny": 1.6 * lex["laughter"],
        "surprising": 1.3 * lex["surprise"],
        "strong_statement": lex["strong_statement"] + NUMBER_WEIGHT * r.get("numbers", 0.0) * per30,
        "emotional": lex["emotional"],
        "explanation": 0.7 * lex["explanation"],
        "gaming_achievement": 1.3 * lex["gaming"],
        "audience_interaction": lex["audience"],
        "conversation": 1.2 * r.get("qa", 0.0) * per30,
        "reaction": 0.5 * min(3.0, r.get("peaks", 0.0)) * per30 if reactive > 0 else 0.0,
        "exciting": 0.5 * r.get("exclam", 0.0) * per30 + 2.0 * max(0.0, energy - 0.65),
    }
    best = max(scores, key=lambda k: scores[k])
    if scores[best] >= CATEGORY_MIN_DENSITY:
        return best
    if energy >= 0.7 or feats.get("visual", 0.0) >= 0.8:
        return "exciting"
    return "other"


def _snippet(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _context_evidence(ctx: SignalContext, first: int, last: int, r: dict[str, float],
                      breakdown: dict[str, float]) -> list[tuple[float, str]]:
    """Reasons (and honest cautions) from the discourse-structure features."""
    c = ctx.context
    ev: list[tuple[float, str]] = []
    span = f"{format_timestamp(ctx.sentences[first].start)}–{format_timestamp(ctx.sentences[last].end)}"
    if r.get("topic", 0.0) >= 0.75 and r.get("topic_coverage", 0.0) >= 0.8:
        ev.append((breakdown.get("topic", 0.0) * 0.8, f"Covers one complete topic ({span})"))
    if r.get("ending", 0.0) >= 0.7 and c.ending_why[last]:
        ev.append((breakdown.get("ending", 0.0) * 0.7,
                   f"{c.ending_why[last][0].upper()}{c.ending_why[last][1:]}: “{_snippet(ctx.sentences[last].text)}”"))
    if r.get("arc", 0.0) >= 0.7 and not r.get("qa"):
        ev.append((breakdown.get("arc", 0.0) * 0.9, "Complete story arc (setup, turn and payoff)"))
    if r.get("standalone", 1.0) >= 0.9 and r.get("topic", 0.0) >= 0.6:
        ev.append((breakdown.get("standalone", 0.0) * 0.3, "Opens without needing earlier context"))
    cautions = []
    if r.get("standalone", 1.0) < 0.6 and c.dependency_why[first]:
        cautions.append(f"Caution: the opening {c.dependency_why[first]}")
    if r.get("ending", 1.0) < 0.35 and c.ending_why[last]:
        cautions.append(f"Caution: {c.ending_why[last]}")
    ev.extend((3.0, text) for text in cautions[:1])
    return ev


def reasons(ctx: SignalContext, w: Window, breakdown: dict[str, float], feats: dict[str, float],
            limit: int = 3) -> list[str]:
    """Top concrete, timestamped evidences for window ``w`` (strongest contribution first)."""
    r = raw_signals(ctx, w)
    ev: list[tuple[float, str]] = []
    quoted: dict[str, int] = {}     # evidence text -> quoted sentence index (one quote per sentence)
    peaks = ctx.peaks_in(w.start, w.end)
    if peaks:
        t = max(peaks, key=ctx.db_at)
        delta = ctx.db_at(t) - ctx.median_db
        if delta >= 6.0:
            ev.append((breakdown.get("audio_energy", 0.0) + 1.0,
                       f"Loud reaction at {format_timestamp(t)} (+{delta:.0f} dB above average)"))
    rng = ctx.sentence_range(w.start, w.end)
    if rng is not None:
        first, last = rng
        dens = lexical_density(r) or 1.0
        for group in LEXICAL_GROUPS:
            hit = next((i for i in range(first, last + 1) if group in ctx.lex_hits[i]), None)
            if hit is None:
                continue
            share = LEX_WEIGHTS[group] * r[f"lex_{group}"] * 30.0 / max(r["duration"], 1.0) / dens
            s = ctx.sentences[hit]
            text = f"{GROUP_LABELS[group]} at {format_timestamp(s.start)}: “{_snippet(s.text)}”"
            ev.append((breakdown.get("lexical", 0.0) * share, text))
            quoted[text] = hit
        qa = next((i for i in range(first, last) if ctx.qa_flags[i]), None)
        if qa is not None:
            share = QA_WEIGHT * r["qa"] * 30.0 / max(r["duration"], 1.0) / dens
            ev.append((breakdown.get("lexical", 0.0) * share + 0.5,
                       f"Question at {format_timestamp(ctx.sentences[qa].start)} answered within the clip"))
        if ctx.hooks[first] >= 0.5:
            text = f"Strong opening hook: “{_snippet(ctx.sentences[first].text)}”"
            ev.append((breakdown.get("hook", 0.0), text))
            quoted[text] = first
        if feats.get("speech", 0.0) >= 0.65:
            ev.append((breakdown.get("speech", 0.0) * 0.8,
                       f"Dense speech ({r['words_per_s']:.1f} words/s) with little dead air"))
        if r["completeness"] >= 0.9:
            ev.append((breakdown.get("completeness", 0.0) * 0.4, "Starts and ends on complete sentences"))
        ev.extend(_context_evidence(ctx, first, last, r, breakdown))
    if r["cuts"] >= 2:
        ev.append((breakdown.get("visual", 0.0) * 0.6 + 0.5,
                   f"{int(r['cuts'])} scene changes in {math.ceil(w.duration)} s"))
    if feats.get("_motion_norm", 0.0) >= 0.7 and ctx.motion.size:
        a = min(ctx.motion.size - 1, int(w.start * ctx.motion_hz))
        b = min(ctx.motion.size, max(a + 1, int(math.ceil(w.end * ctx.motion_hz))))
        t = (a + int(np.argmax(ctx.motion[a:b]))) / ctx.motion_hz
        ev.append((breakdown.get("visual", 0.0) * 0.5, f"High on-screen motion around {format_timestamp(t)}"))
    if "retention" in r:
        ev.append((breakdown.get("retention", 0.0) + 0.5,
                   f"Real YouTube retention here: {100 * r['retention']:.0f}% average audience watch ratio "
                   "(YouTube Analytics)"))
    out: list[str] = []
    used_sentences: set[int] = set()
    for _, text in sorted(ev, key=lambda e: -e[0]):
        sentence = quoted.get(text)
        if text in out or (sentence is not None and sentence in used_sentences):
            continue
        out.append(text)
        if sentence is not None:
            used_sentences.add(sentence)
        if len(out) >= limit:
            break
    return out
