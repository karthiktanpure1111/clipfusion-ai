"use client";

/**
 * Real-time 9:16 output preview with the same geometry as the export renderer:
 * background (blur/colour/image) -> layers by z (fit, radius, border, shadow) -> optional guides.
 * Clips are exported clean (no burned-in captions), so nothing is drawn on top of the layers.
 * Draws on every player frame (rAF only while playing) and whenever the inputs change; the
 * backing store follows the container size and devicePixelRatio.
 */
import { useEffect, useEffectEvent, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

import { visibleFaces } from "@/lib/framing/geometry";
import type { ClipVisionAnalysis, CompiledLayout } from "@/lib/framing/types";
import { composeFrame, drawFaceBoxes, drawSafeArea, Scratch } from "./compose";
import { usePlayerState, type ClipPlayer } from "./player";

export interface ClipPreviewCanvasProps {
  player: ClipPlayer;
  layout: CompiledLayout | null;
  sourceWidth: number;
  sourceHeight: number;
  vision?: ClipVisionAnalysis | null;
  showSafeArea?: boolean;
  showFaces?: boolean;
  backgroundImageUrl?: string | null;
  className?: string;
  /** Interactive overlay, positioned over the 9:16 frame (absolute, inset 0). */
  children?: ReactNode;
}

const DEFAULT_SAFE = { top: 0.08, bottom: 0.14, left: 0.06, right: 0.08 };

export function ClipPreviewCanvas({
  player,
  layout,
  sourceWidth,
  sourceHeight,
  vision,
  showSafeArea,
  showFaces,
  backgroundImageUrl,
  className,
  children,
}: ClipPreviewCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [bgImage, setBgImage] = useState<HTMLImageElement | null>(null);
  const [scratch] = useState(() => new Scratch());
  const snap = usePlayerState(player);
  const aspect = layout ? layout.width / layout.height : 9 / 16;

  // Fit a 9:16 box into the container.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      const w = Math.min(width, height * aspect);
      setBox({ w: Math.floor(w), h: Math.floor(w / aspect) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [aspect]);

  useEffect(() => {
    if (!backgroundImageUrl) return;
    let alive = true;
    const img = new Image();
    img.onload = () => alive && setBgImage(img);
    img.src = backgroundImageUrl;
    return () => {
      alive = false;
    };
  }, [backgroundImageUrl]);

  const draw = useEffectEvent(() => {
    const cv = canvasRef.current;
    if (!cv || !layout || box.w === 0) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    const pw = Math.round(box.w * dpr);
    const ph = Math.round(box.h * dpr);
    if (cv.width !== pw || cv.height !== ph) {
      cv.width = pw;
      cv.height = ph;
    }
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    const ds = pw / layout.width;
    const t = player.time;
    composeFrame(ctx, { video: player.video, layout, t, ds, scratch, bgImage: backgroundImageUrl ? bgImage : null });
    if (showSafeArea) drawSafeArea(ctx, layout.width, layout.height, DEFAULT_SAFE, ds);
    if (showFaces && vision) drawFaceBoxes(ctx, layout.width, layout.height, visibleFaces(vision.tracks, layout, t, sourceWidth, sourceHeight), ds);
  });

  useEffect(() => player.onFrame(() => draw()), [player]);
  useEffect(() => {
    draw();
  }, [layout, showSafeArea, showFaces, box, bgImage, vision, sourceWidth, sourceHeight]);

  return (
    <div ref={containerRef} className={cn("relative flex min-h-0 items-center justify-center", className)}>
      <div
        className="relative overflow-hidden rounded-2xl bg-black shadow-2xl ring-1 ring-white/10"
        style={{ width: box.w || undefined, height: box.h || undefined, aspectRatio: box.w ? undefined : `${aspect}` }}
      >
        <canvas
          ref={canvasRef}
          role="img"
          aria-label="Live 9:16 output preview"
          className="absolute inset-0 size-full"
          onClick={() => player.toggle()}
        />
        {snap.error && (
          <p className="absolute inset-x-4 top-1/2 -translate-y-1/2 rounded-lg bg-black/70 p-3 text-center text-xs text-muted-foreground">
            The preview video could not be loaded (the link may have expired). Reload the page to renew it.
          </p>
        )}
        {!snap.ready && !snap.error && (
          <p className="absolute inset-x-4 top-1/2 -translate-y-1/2 text-center text-xs text-muted-foreground">Loading preview…</p>
        )}
        {box.w > 0 && children}
      </div>
    </div>
  );
}

