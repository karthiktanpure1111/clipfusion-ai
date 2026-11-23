"use client";

/** AI smart-crop settings: primary person (live face thumbnails), speaker switching, camera feel. */
import { useEffect, useEffectEvent, useRef } from "react";
import { Sparkles, User } from "lucide-react";
import { cn } from "@/lib/utils";

import { LabeledSlider, Panel, ToggleRow } from "@/components/preview/controls";
import type { ClipPlayer } from "@/components/preview/player";
import { faceRectAt } from "@/lib/framing/geometry";
import type { ClipVisionAnalysis, FaceTrack, SmartCropConfig } from "@/lib/framing/types";
import { trackLabel } from "./SourceOverlays";

/** Live thumbnail of a face cropped from the playing video (keeps the last seen frame). */
function FaceThumb({ player, track }: { player: ClipPlayer; track: FaceTrack }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const draw = useEffectEvent(() => {
    const cv = ref.current;
    const v = player.video;
    if (!cv || !v || !player.frameReady) return;
    const r = faceRectAt(track, player.time);
    if (!r) return;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    const vw = v.videoWidth;
    const vh = v.videoHeight;
    const side = Math.max(r.w * vw, r.h * vh) * 1.6;
    const cx = (r.x + r.w / 2) * vw;
    const cy = (r.y + r.h / 2) * vh;
    ctx.drawImage(v, cx - side / 2, cy - side / 2, side, side, 0, 0, cv.width, cv.height);
  });
  useEffect(() => {
    draw(); // also while paused
    return player.onFrame(() => draw());
  }, [player]);
  return (
    <span className="relative grid size-12 place-items-center overflow-hidden rounded-xl bg-muted">
      <User className="size-5 text-muted-foreground/50" aria-hidden />
      <canvas ref={ref} width={96} height={96} className="absolute inset-0 size-full" aria-hidden />
    </span>
  );
}

export function SmartControls({
  player,
  smart,
  vision,
  onChange,
}: {
  player: ClipPlayer;
  smart: SmartCropConfig;
  vision: ClipVisionAnalysis | null;
  onChange: (patch: Partial<SmartCropConfig>) => void;
}) {
  const tracks = vision?.tracks ?? [];
  const seekTo = (track: FaceTrack) => track.boxes[0] && player.seek(track.boxes[0].t);
  return (
    <Panel title="AI Smart Crop">
      <div className="space-y-4">
        <div>
          <p className="mb-2 text-xs font-medium">Primary person</p>
          {tracks.length === 0 ? (
            <p className="text-[11px] text-muted-foreground">No faces were detected in this clip. Re-run face detection or choose another mode.</p>
          ) : (
            <div role="radiogroup" aria-label="Primary person" className="flex flex-wrap gap-2">
              <button
                type="button"
                role="radio"
                aria-checked={smart.primary_track_id === null}
                onClick={() => onChange({ primary_track_id: null })}
                className={cn(
                  "flex w-16 flex-col items-center gap-1 rounded-xl p-1.5 text-[10px] outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring",
                  smart.primary_track_id === null && "bg-muted ring-2 ring-ig-pink",
                )}
              >
                <span className="bg-ig-gradient grid size-12 place-items-center rounded-xl text-white">
                  <Sparkles className="size-5" />
                </span>
                Auto
              </button>
              {tracks.map((tr, i) => (
                <button
                  key={tr.id}
                  type="button"
                  role="radio"
                  aria-checked={smart.primary_track_id === tr.id}
                  onClick={() => onChange({ primary_track_id: tr.id })}
                  onDoubleClick={() => seekTo(tr)}
                  title={`${trackLabel(tr, i)} · on screen ${Math.round(tr.coverage * 100)}% of the clip (double-click to jump)`}
                  className={cn(
                    "relative flex w-16 flex-col items-center gap-1 rounded-xl p-1.5 text-[10px] outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring",
                    smart.primary_track_id === tr.id && "bg-muted ring-2 ring-ig-pink",
                  )}
                >
                  <FaceThumb player={player} track={tr} />
                  <span className="w-full truncate text-center">{trackLabel(tr, i)}</span>
                  {tr.is_primary && <span className="absolute top-1 right-1 size-2 rounded-full bg-ig-orange" title="Most prominent" />}
                </button>
              ))}
            </div>
          )}
        </div>
        <ToggleRow
          label="Automatic speaker switching"
          description="Cuts to whoever is talking, with a minimum shot length so the camera does not jump between faces. Turn off to always follow the primary person."
          checked={smart.auto_switch}
          onChange={(v) => onChange({ auto_switch: v })}
        />
        {smart.auto_switch && (
          <LabeledSlider
            label="Minimum shot length"
            value={smart.min_shot_seconds}
            min={0.5}
            max={10}
            step={0.5}
            format={(v) => `${v.toFixed(1)} s`}
            onChange={(v) => onChange({ min_shot_seconds: v })}
          />
        )}
        <LabeledSlider label="Zoom" value={smart.zoom} min={1} max={3} step={0.05} format={(v) => `${v.toFixed(2)}×`} onChange={(v) => onChange({ zoom: v })} />
        <LabeledSlider
          label="Smoothing"
          hint="Higher = calmer camera movement."
          value={smart.smoothing}
          min={0}
          max={1}
          step={0.01}
          format={(v) => `${Math.round(v * 100)}%`}
          onChange={(v) => onChange({ smoothing: v })}
        />
        <LabeledSlider
          label="Dead zone"
          hint="How far the person can move before the camera follows."
          value={smart.deadzone}
          min={0}
          max={0.4}
          step={0.01}
          format={(v) => `${Math.round(v * 100)}%`}
          onChange={(v) => onChange({ deadzone: v })}
        />
        <LabeledSlider
          label="Headroom"
          hint="Space kept above the head when zoomed in."
          value={smart.headroom}
          min={0}
          max={0.5}
          step={0.01}
          format={(v) => `${Math.round(v * 100)}%`}
          onChange={(v) => onChange({ headroom: v })}
        />
        <p className="text-[11px] text-muted-foreground">The camera path is recomputed by the server after each change; the preview updates when it is ready.</p>
      </div>
    </Panel>
  );
}
