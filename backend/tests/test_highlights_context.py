"""Contextual clip-selection signals: topic segmentation, standalone openings, endings,
question -> answer, story arcs, near-duplicates -- and the end-to-end preference for a
complete exchange over a mid-thought fragment."""

from __future__ import annotations

from app.modules.highlights.context import (
    build_context,
    closing_quality,
    opening_dependency,
    topic_boundaries,
    window_context,
)
from app.modules.highlights.dedupe import select_diverse, signature, text_similarity
from app.modules.highlights.detector import detect_highlights
from app.modules.highlights.features import SignalContext, Window, raw_signals
from app.modules.highlights.scoring import normalise, score
from app.modules.highlights.sentences import Sentence, build_sentences
from app.schemas.media import AnalysisOptions, AudioAnalysis, HighlightCandidate, MediaInfo, VisualActivity
from tests.test_highlights_detector import build_transcript

DURATION = 200.0

FRAGMENT = [   # leans on earlier context, ends mid-thought
    "So he did it again with the same sword.",
    "That was the reason it worked so well honestly.",
    "And it was the craziest thing I have ever seen in this game.",
    "But then he went back to the old castle and",
]
EXCHANGE = [   # self-contained question -> answer -> conclusion
    "What is the biggest mistake people make in this game?",
    "They rush every fight without a plan.",
    "Patience beats speed every single time, honestly.",
    "If you remember only one thing from today, remember that.",
]
FILLER_A = ["We are checking the inventory screen right now.", "The map is loading slowly on this computer."]
FILLER_B = ["Let me grab some water before the next part.", "The weather outside is pretty grey today."]


def _lines() -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    t = 1.0

    def add(texts: list[str], pause_after: float = 0.4) -> None:
        nonlocal t
        for text in texts:
            out.append((t, text))
            t += len(text.split()) * 0.35 + pause_after

    for _ in range(3):
        add(FILLER_A)
    t += 2.5
    add(FRAGMENT[:-1])
    add(FRAGMENT[-1:], pause_after=0.2)
    add(FILLER_B)
    t += 2.5
    add(EXCHANGE)
    t += 2.5
    for _ in range(4):
        add(FILLER_B + FILLER_A)
    return [(a, b) for a, b in out if a < DURATION - 10]


def _signals() -> dict:
    transcript = build_transcript(_lines())
    n = int(DURATION * 10)
    audio = AudioAnalysis(sample_hz=10.0, rms_db=[-24.0] * n, silences=[], peak_times=[], speech_ratio=1.0,
                          mean_db=-24.0)
    return {"transcript": transcript, "audio": audio, "scenes": [],
            "visual": VisualActivity(sample_hz=1.0, motion=[0.02] * int(DURATION), brightness=[0.5] * int(DURATION)),
            "media": MediaInfo(duration=DURATION, width=1280, height=720, fps=30.0, has_audio=True)}


def _span(transcript, texts: list[str]) -> tuple[float, float]:
    ss = build_sentences(transcript)
    first = next(s for s in ss if s.text.startswith(texts[0].split()[0]) and texts[0][:20] in s.text)
    last = next(s for s in ss if texts[-1][:20] in s.text and s.start >= first.start)
    return first.start, last.end


def _s(text: str, start: float = 0.0) -> Sentence:
    from app.modules.transcription.cleanup import ends_sentence

    return Sentence(start=start, end=start + 0.35 * len(text.split()), text=text, terminal=ends_sentence(text))


def test_opening_dependency_detects_context_leaning_starts() -> None:
    for text in ("So he did it again.", "And that is why it failed.", "That was the reason.", "They said no.",
                 "This is why you lost."):
        dep, why = opening_dependency(_s(text), _s("Previous line."), 0.2, False)
        assert dep >= 0.35 and why, text
    for text in ("What is the biggest mistake people make?", "Let me tell you a story about my first stream.",
                 "This next part is really funny."):
        dep, _ = opening_dependency(_s(text), _s("Previous line."), 1.0, False)
        assert dep <= 0.2, text
    dep, why = opening_dependency(_s("Yes, exactly."), _s("Is it hard?"), 0.0, True)
    assert dep >= 0.7 and "repl" in why or "answer" in why


def test_closing_quality_prefers_conclusions_and_punchlines() -> None:
    good, why = closing_quality(_s("If you remember one thing, remember that."), None, 1.0,
                                question_answered_later=False)
    punch, _ = closing_quality(_s("No way!"), None, 0.3, question_answered_later=False)
    setup, why_setup = closing_quality(_s("Let me tell you what happened next:"), _s("He fell.", 9.0), 0.0,
                                       question_answered_later=False)
    dangling, _ = closing_quality(_s("He went back to the castle and"), _s("and then he left", 2.5), 0.0,
                                  question_answered_later=False)
    question, why_q = closing_quality(_s("Why did that work?"), _s("Because the shield resets.", 2.0), 0.0,
                                      question_answered_later=True)
    assert good >= 0.85 and "conclusion" in why
    assert punch > setup and punch > dangling
    assert setup <= 0.2 and "set-up" in why_setup
    assert question <= 0.2 and "answered after" in why_q


def test_topic_boundaries_follow_topic_shifts_and_pauses() -> None:
    a = ["The sourdough starter needs warm water and flour.", "Feed the sourdough starter twice a day.",
         "A healthy starter doubles in size with bubbles."]
    b = ["Now let's talk about the new graphics card.", "The graphics card runs every game at high settings.",
         "Its fans stay quiet even under heavy load."]
    ss, t = [], 0.0
    for i, text in enumerate(a + b):
        ss.append(_s(text, t))
        t = ss[-1].end + (2.5 if i == len(a) - 1 else 0.3)
    bounds = topic_boundaries(ss)
    gap = len(a)
    assert bounds[gap] >= 0.5
    assert bounds[gap] == max(bounds[1:-1])
    ctx = build_context(ss)
    assert [b for _, b in ctx.segment_bounds][0] == gap - 1
    whole = window_context(ctx, 0, gap - 1)
    straddle = window_context(ctx, 1, gap)
    assert whole["topic"] > straddle["topic"] and whole["topic_coverage"] == 1.0


def test_question_answer_and_story_arcs() -> None:
    story = ["Let me tell you a story from three years ago.", "I was working two jobs and nobody watched.",
             "Then one night a viewer sent me a message.", "That message changed my life forever."]
    ss, t = [], 0.0
    for text in story + EXCHANGE:
        ss.append(_s(text, t))
        t = ss[-1].end + 0.4
    ctx = build_context(ss)
    assert window_context(ctx, 0, 3)["arc"] >= 0.99
    assert window_context(ctx, 0, 1)["arc"] < 0.5
    assert window_context(ctx, 4, 7)["arc"] == 1.0               # question answered inside
    assert window_context(ctx, 5, 7)["standalone"] < 0.6         # starts with the bare answer


def test_complete_exchange_beats_mid_thought_fragment() -> None:
    sig = _signals()
    ctx = SignalContext(**sig)
    frag = Window(*_span(sig["transcript"], FRAGMENT), 15.0)
    exch = Window(*_span(sig["transcript"], EXCHANGE), 15.0)
    raws = [raw_signals(ctx, frag), raw_signals(ctx, exch)]
    feats = normalise(ctx, raws, speech_mode=True)
    (s_frag, b_frag), (s_exch, b_exch) = score(feats[0]), score(feats[1])
    assert s_exch > s_frag + 10, (s_exch, s_frag)
    for key in ("standalone", "ending", "arc"):
        assert b_exch[key] > b_frag[key], key
    assert abs(sum(b_exch.values()) - s_exch) < 0.1


def test_detector_prefers_the_complete_exchange() -> None:
    sig = _signals()
    options = AnalysisOptions(clip_durations=[15.0], min_duration=8.0, max_duration=30.0, max_clips=3, use_llm=False)
    cands = detect_highlights(**sig, options=options)
    assert cands
    top = cands[0]
    a, b = _span(sig["transcript"], EXCHANGE)
    assert top.start <= a + 0.2 and top.end >= b - 0.2, (top.start, top.end, top.transcript)
    assert not top.transcript.lower().startswith(("so he", "and ", "but "))
    assert any(r.startswith(("Covers one complete topic", "Ends on", "Question at", "Opens without"))
               for r in top.reasons)
    assert all(8.0 - 1e-6 <= c.end - c.start <= 30.0 + 1e-6 for c in cands)
    frag_start = _span(sig["transcript"], FRAGMENT)[0]
    for c in cands:   # a clip may include the fragment only if it does not start on "So he ..."
        assert abs(c.start - frag_start) > 0.5 or c.score < top.score


def test_near_duplicate_transcripts_are_suppressed() -> None:
    text = " ".join(EXCHANGE)
    a = HighlightCandidate(start=10, end=30, score=80, transcript=text)
    b = HighlightCandidate(start=300, end=320, score=70, transcript="Okay. " + text)
    c = HighlightCandidate(start=500, end=520, score=60, transcript=" ".join(FRAGMENT))
    assert text_similarity(signature(a.transcript), signature(b.transcript)) > 0.9
    kept = select_diverse([a, b, c], 0.25)
    assert kept == [a, c]
    repeated = HighlightCandidate(start=600, end=620, score=50, transcript=" ".join([FILLER_A[0]] * 4))
    containing = HighlightCandidate(start=700, end=730, score=55, transcript=FILLER_A[0] + " " + text)
    assert len(select_diverse([containing, repeated], 0.25)) == 2   # one shared sentence is not a duplicate
