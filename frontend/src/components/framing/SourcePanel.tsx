"use client";

/**
 * ORIGINAL video panel: the proxy frame at the source aspect with a mode-specific editable
 * overlay (manual crop / facecam + gameplay / pip sources / AI camera window / full frame),
 * hatched "Outside final frame" areas, face boxes and an optional activity heatmap.
 */
import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import { Expand } from "lucide-react";

import { normAspect, roundRect } from "@/lib/framing/editing";
import { faceRectAt, rectAt, visibleSourceRect } from "@/lib/framing/geometry";
import { panelAspects } from "@/lib/framing/layout";
import type { ClipVisionAnalysis, CompiledLayout, FramingConfig, Rect } from "@/lib/framing/types";
import { usePlayerState, type ClipPlayer } from "@/components/preview/player";
import { RectEditor } from "./RectEditor";
import { ActivityHeatmap, FaceBoxes, OutsideMask } from "./SourceOverlays";
import type { FramingEditorState } from "./useFramingEditor";

export const FACECAM_COLOR = "#DD2A7B";
export const GAMEPLAY_COLOR = "#515BD4";
export const CROP_COLOR = "#FF0033";

export function SourcePanel({
  player,
  editor,
  vision,
  sourceW,
  sourceH,
  showFaces,
  showHeatmap,
}: {
  player: ClipPlayer;
  editor: FramingEditorState;
  vision: ClipVisionAnalysis | null;
  sourceW: number;
  sourceH: number;
  showFaces: boolean;
  showHeatmap: boolean;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const { time: t } = usePlayerState(player);
  const { config, compiled } = editor;
  const aspect = sourceW / sourceH;

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => {
      const w = Math.min(e.contentRect.width, e.contentRect.height * aspect);
      setBox({ w: Math.floor(w), h: Math.floor(w / aspect) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [aspect]);

  const draw = useEffectEvent(() => {
    const cv = canvasRef.current;
    const v = player.video;
    if (!cv || !box.w) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const pw = Math.round(box.w * dpr);
    const ph = Math.round(box.h * dpr);
    if (cv.width !== pw || cv.height !== ph) {
      cv.width = pw;
      cv.height = ph;
    }
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = "#0b0b0e";
    ctx.fillRect(0, 0, pw, ph);
    if (v && player.frameReady) ctx.drawImage(v, 0, 0, pw, ph);
  });
  useEffect(() => player.onFrame(() => draw()), [player]);
  useEffect(() => {
    draw();
  }, [box]);

  const visible = useMemo(
    () =>
      config.mode === "full_frame"
        ? []
        : compiled.layers.map((l) => visibleSourceRect(l, t, sourceW, sourceH, compiled.width, compiled.height)),
    [config.mode, compiled, t, sourceW, sourceH],
  );
  const faces = useMemo(
    () => (vision?.tracks ?? []).map((tr) => faceRectAt(tr, t)).filter((r): r is Rect => !!r),
    [vision, t],
  );
  const snapTargets = faces.map((f) => ({ x: f.x + f.w / 2, y: f.y + f.h / 2 }));
  const outAspect = normAspect(config.output_width / config.output_height, sourceW, sourceH);

  return (
    <div ref={wrapRef} className="relative flex min-h-0 flex-1 items-center justify-center">
      <div className="relative overflow-visible rounded-xl bg-black ring-1 ring-white/10" style={{ width: box.w || undefined, height: box.h || undefined }}>
        <canvas ref={canvasRef} className="absolute inset-0 size-full rounded-xl" role="img" aria-label="Original video at source aspect" />
        {showHeatmap && vision?.activity && <ActivityHeatmap grid={vision.activity} />}
        <OutsideMask visible={visible} />
        {showFaces && vision && <FaceBoxes tracks={vision.tracks} t={t} primaryId={config.smart.primary_track_id} />}
        {box.w > 0 && (
          <ModeOverlay editor={editor} compiled={compiled} t={t} sourceW={sourceW} sourceH={sourceH} outAspect={outAspect} snapTargets={snapTargets} />
        )}
      </div>
    </div>
  );
}

function ModeOverlay({
  editor,
  compiled,
  t,
  sourceW,
  sourceH,
  outAspect,
  snapTargets,
}: {
  editor: FramingEditorState;
  compiled: CompiledLayout;
  t: number;
  sourceW: number;
  sourceH: number;
  outAspect: number;
  snapTargets: { x: number; y: number }[];
}) {
  const { config, update } = editor;
  const set = (fn: (c: FramingConfig) => FramingConfig) => update(fn);
  switch (config.mode) {
    case "manual": {
      const m = config.manual;
      return (
        <RectEditor
          rect={m.crop}
          aspect={m.lock_aspect ? outAspect : null}
          color={CROP_COLOR}
          label={m.lock_aspect ? "Final frame · 9:16" : "Final frame · free aspect"}
          snapTargets={snapTargets}
          onChange={(r) => set((c) => ({ ...c, manual: { ...c.manual, crop: roundRect(r), keyframes: [] } }))}
        />
      );
    }
    case "facecam_gameplay": {
      const [, gameAspect] = panelAspects(config);
      const fg = config.facecam_gameplay;
      return (
        <>
          <RectEditor
            rect={fg.gameplay}
            aspect={normAspect(gameAspect, sourceW, sourceH)}
            color={GAMEPLAY_COLOR}
            label="Gameplay"
            snapTargets={snapTargets}
            onChange={(r) => set((c) => ({ ...c, facecam_gameplay: { ...c.facecam_gameplay, gameplay: roundRect(r) } }))}
          />
          <RectEditor
            rect={fg.facecam}
            color={FACECAM_COLOR}
            label="Facecam"
            snapTargets={snapTargets}
            onChange={(r) => set((c) => ({ ...c, facecam_gameplay: { ...c.facecam_gameplay, facecam: roundRect(r) } }))}
          />
        </>
      );
    }
    case "pip": {
      const p = config.pip;
      const destAspect = (p.overlay_dest.w * config.output_width) / (p.overlay_dest.h * config.output_height);
      return (
        <>
          <RectEditor
            rect={p.main}
            color={GAMEPLAY_COLOR}
            label="Main video"
            dashed
            snapTargets={snapTargets}
            onChange={(r) => set((c) => ({ ...c, pip: { ...c.pip, main: roundRect(r) } }))}
          />
          <RectEditor
            rect={p.overlay_source}
            aspect={normAspect(destAspect, sourceW, sourceH)}
            color={FACECAM_COLOR}
            label="Facecam overlay source"
            snapTargets={snapTargets}
            onChange={(r) => set((c) => ({ ...c, pip: { ...c.pip, overlay_source: roundRect(r) } }))}
          />
        </>
      );
    }
    case "smart": {
      const main = compiled.layers[0];
      const r = main ? rectAt(main, t) : null;
      if (!r) return null;
      return (
        <div
          className="pointer-events-none absolute rounded-md"
          style={{ left: `${r.x * 100}%`, top: `${r.y * 100}%`, width: `${r.w * 100}%`, height: `${r.h * 100}%`, boxShadow: "0 0 24px #DD2A7B66" }}
          role="img"
          aria-label="AI camera window at the current time"
        >
          {/* nested: .gradient-border sets position: relative and would undo `absolute` */}
          <span className="gradient-border block size-full rounded-md" />
          <span className="bg-ig-gradient absolute -top-6 left-0 rounded-md px-1.5 py-0.5 text-[10px] font-semibold whitespace-nowrap text-white">
            AI camera {main.keyframes.length ? "" : "(waiting for analysis)"}
          </span>
        </div>
      );
    }
    default:
      return (
        <div className="pointer-events-none absolute inset-0 rounded-xl ring-2 ring-success/70">
          <span className="absolute top-2 left-2 flex items-center gap-1 rounded-md bg-success/90 px-2 py-0.5 text-[10px] font-semibold text-black">
            <Expand className="size-3" /> Whole frame kept — nothing is cropped
          </span>
        </div>
      );
  }
}
