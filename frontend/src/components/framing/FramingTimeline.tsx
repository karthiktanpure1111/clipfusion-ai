"use client";

/**
 * Timeline over the clip: transport, scrubber, face-track presence lanes, speaker segments and
 * warning time ranges (click a range or anywhere to seek). The playhead is moved directly on
 * every video frame (no React re-render).
 */
import { useEffect, useMemo, useRef, type PointerEvent } from "react";
import { cn } from "@/lib/utils";

import { PlaybackBar } from "@/components/preview/PlaybackBar";
import type { ClipPlayer } from "@/components/preview/player";
import { formatTime } from "@/components/preview/player";
import { SEVERITY_STYLE } from "@/components/preview/WarningBadge";
import type { ClipVisionAnalysis, FramingWarning } from "@/lib/framing/types";
import { trackLabel } from "./SourceOverlays";

const LANE_COLORS = ["#FCAF45", "#DD2A7B", "#515BD4", "#22C55E", "#00E5FF", "#8134AF"];

function presence(times: number[], gap: number): [number, number][] {
  const out: [number, number][] = [];
  for (const t of times) {
    const last = out[out.length - 1];
    if (last && t - last[1] <= gap) last[1] = t;
    else out.push([t, t]);
  }
  return out.map(([a, b]) => [a, b + gap / 2]);
}

export function FramingTimeline({
  player,
  duration,
  vision,
  warnings,
}: {
  player: ClipPlayer;
  duration: number;
  vision: ClipVisionAnalysis | null;
  warnings: FramingWarning[];
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const headRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const d = Math.max(duration, 0.01);

  useEffect(
    () =>
      player.onFrame(() => {
        if (headRef.current) headRef.current.style.left = `${(player.time / d) * 100}%`;
      }),
    [player, d],
  );

  const lanes = useMemo(() => {
    const gap = vision ? Math.max(2 / Math.max(vision.sample_fps, 0.1), 0.6) : 1;
    return (vision?.tracks ?? []).map((tr, i) => ({
      id: tr.id,
      label: trackLabel(tr, i),
      color: LANE_COLORS[i % LANE_COLORS.length],
      ranges: presence(
        tr.boxes.map((b) => b.t),
        gap,
      ),
    }));
  }, [vision]);
  const colorOf = (id: number) => lanes.find((l) => l.id === id)?.color ?? "#888";

  const seekAt = (e: PointerEvent<HTMLDivElement>) => {
    const r = trackRef.current?.getBoundingClientRect();
    if (!r) return;
    player.seek(((e.clientX - r.left) / r.width) * d);
  };
  const pct = (t: number) => `${(Math.min(Math.max(t, 0), d) / d) * 100}%`;
  const ticks = Array.from({ length: Math.floor(d / 5) + 1 }, (_, i) => i * 5);

  return (
    <div className="glass space-y-2 rounded-2xl border border-white/[0.06] bg-card/70 p-3">
      <PlaybackBar player={player} />
      <div className="flex gap-2">
        <div className="w-20 shrink-0 space-y-1 pt-4 text-[10px] text-muted-foreground">
          {lanes.map((l) => (
            <div key={l.id} className="flex h-3 items-center gap-1 truncate">
              <span className="size-2 rounded-full" style={{ background: l.color }} />
              {l.label}
            </div>
          ))}
          {vision && vision.speaker_timeline.length > 0 && <div className="h-3">Speaking</div>}
          <div className="h-3">Warnings</div>
        </div>
        <div
          ref={trackRef}
          role="slider"
          tabIndex={0}
          aria-label="Clip timeline"
          aria-valuemin={0}
          aria-valuemax={d}
          aria-valuenow={0}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
              e.preventDefault();
              player.seek(player.time + (e.key === "ArrowLeft" ? -0.5 : 0.5));
            }
          }}
          onPointerDown={(e) => {
            dragging.current = true;
            e.currentTarget.setPointerCapture(e.pointerId);
            seekAt(e);
          }}
          onPointerMove={(e) => dragging.current && seekAt(e)}
          onPointerUp={() => (dragging.current = false)}
          className="relative min-w-0 flex-1 cursor-pointer touch-none rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <div className="relative h-3 text-[9px] text-muted-foreground">
            {ticks.map((t) => (
              <span key={t} className="absolute -translate-x-1/2" style={{ left: pct(t) }}>
                {formatTime(t)}
              </span>
            ))}
          </div>
          <div className="mt-1 space-y-1">
            {lanes.map((l) => (
              <div key={l.id} className="relative h-3 rounded bg-muted/60">
                {l.ranges.map(([a, b], i) => (
                  <span key={i} className="absolute inset-y-0 rounded" style={{ left: pct(a), width: `calc(${pct(b)} - ${pct(a)})`, background: l.color, opacity: 0.75 }} />
                ))}
              </div>
            ))}
            {vision && vision.speaker_timeline.length > 0 && (
              <div className="relative h-3 rounded bg-muted/60">
                {vision.speaker_timeline.map((s, i) => (
                  <span
                    key={i}
                    title={`Speaker: ${lanes.find((l) => l.id === s.track_id)?.label ?? s.track_id}`}
                    className="absolute inset-y-0 rounded border-r border-black/40"
                    style={{ left: pct(s.start), width: `calc(${pct(s.end)} - ${pct(s.start)})`, background: colorOf(s.track_id) }}
                  />
                ))}
              </div>
            )}
            <div className="relative h-3 rounded bg-muted/60">
              {warnings.flatMap((w, wi) =>
                w.time_ranges.map(([a, b], i) => (
                  <span
                    key={`${wi}-${i}`}
                    title={w.message}
                    className={cn("absolute inset-y-0 rounded opacity-80", SEVERITY_STYLE[w.severity].bar)}
                    style={{ left: pct(a), width: `max(3px, calc(${pct(b)} - ${pct(a)}))` }}
                  />
                )),
              )}
            </div>
          </div>
          <div ref={headRef} className="pointer-events-none absolute top-0 bottom-0 w-0.5 -translate-x-1/2 rounded bg-primary shadow-[0_0_8px_#FF0033]" style={{ left: 0 }}>
            <span className="absolute -top-1 left-1/2 size-2.5 -translate-x-1/2 rotate-45 rounded-[2px] bg-primary" />
          </div>
        </div>
      </div>
    </div>
  );
}
