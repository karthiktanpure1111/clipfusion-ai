"use client";

/** Overlays for the ORIGINAL video panel: outside-frame hatching, face boxes, activity heatmap. */
import { useId } from "react";

import { faceRectAt } from "@/lib/framing/geometry";
import type { ActivityGrid, FaceTrack, Rect } from "@/lib/framing/types";

export function trackLabel(track: FaceTrack, index: number): string {
  if (track.label) return track.label;
  if (track.is_facecam) return "Facecam";
  return `Person ${index + 1}`;
}

/** Everything outside the visible source regions is dimmed with a hatched pattern. */
export function OutsideMask({ visible }: { visible: Rect[] }) {
  const id = useId().replace(/:/g, "");
  if (!visible.length) return null;
  const main = visible.reduce((a, b) => (b.w * b.h > a.w * a.h ? b : a));
  const leftSpace = main.x;
  const rightSpace = 1 - main.x - main.w;
  const labelLeft = leftSpace >= rightSpace;
  const showLabel = Math.max(leftSpace, rightSpace) > 0.1 || main.y > 0.12 || 1 - main.y - main.h > 0.12;
  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden>
      <svg className="absolute inset-0 size-full">
        <defs>
          <pattern id={`p${id}`} width="12" height="12" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="12" height="12" fill="rgba(8,8,12,0.62)" />
            <line x1="0" y1="0" x2="0" y2="12" stroke="rgba(255,255,255,0.13)" strokeWidth="3" />
          </pattern>
          <mask id={`q${id}`}>
            <rect width="100%" height="100%" fill="white" />
            {visible.map((r, i) => (
              <rect key={i} x={`${r.x * 100}%`} y={`${r.y * 100}%`} width={`${r.w * 100}%`} height={`${r.h * 100}%`} fill="black" />
            ))}
          </mask>
        </defs>
        <rect width="100%" height="100%" fill={`url(#p${id})`} mask={`url(#q${id})`} />
      </svg>
      {showLabel && (
        <span
          className="absolute top-2 rounded-md bg-black/70 px-2 py-0.5 text-[10px] font-medium tracking-wide text-white/80 uppercase"
          style={labelLeft ? { left: 8 } : { right: 8 }}
        >
          Outside final frame
        </span>
      )}
    </div>
  );
}

export function FaceBoxes({ tracks, t, primaryId }: { tracks: FaceTrack[]; t: number; primaryId: number | null }) {
  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden>
      {tracks.map((track, i) => {
        const r = faceRectAt(track, t);
        if (!r) return null;
        const primary = primaryId === null ? track.is_primary : primaryId === track.id;
        const tags = [trackLabel(track, i), primary ? "Primary" : null].filter(Boolean);
        return (
          <div
            key={track.id}
            className="absolute rounded-md border-2"
            style={{
              left: `${r.x * 100}%`,
              top: `${r.y * 100}%`,
              width: `${r.w * 100}%`,
              height: `${r.h * 100}%`,
              borderColor: primary ? "#FCAF45" : "rgba(255,255,255,0.8)",
            }}
          >
            <span
              className="absolute top-full left-0 mt-0.5 rounded px-1 text-[9px] font-semibold whitespace-nowrap text-black"
              style={{ background: primary ? "#FCAF45" : "rgba(255,255,255,0.85)" }}
            >
              {tags.join(" · ")}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export function ActivityHeatmap({ grid }: { grid: ActivityGrid }) {
  const max = Math.max(...grid.values, 1e-9);
  return (
    <div
      className="pointer-events-none absolute inset-0 grid mix-blend-screen"
      style={{ gridTemplateColumns: `repeat(${grid.cols}, 1fr)`, gridTemplateRows: `repeat(${grid.rows}, 1fr)` }}
      aria-hidden
    >
      {grid.values.map((v, i) => (
        <span key={i} style={{ background: `rgba(221,42,123,${(0.55 * v) / max})` }} />
      ))}
    </div>
  );
}
