"use client";

/** Per-mode settings (manual, facecam + gameplay, picture-in-picture, full frame). */
import { ArrowDownUp, Crosshair, Lock, Unlock } from "lucide-react";

import { Button } from "@/components/ui/button";
import { ColorField, LabeledSlider, Panel, Segmented, ToggleRow } from "@/components/preview/controls";
import { fitRectToAspect, normAspect, roundRect, zoomOf, zoomRect } from "@/lib/framing/editing";
import { faceRectAt } from "@/lib/framing/geometry";
import { panelAspects } from "@/lib/framing/layout";
import type { ClipVisionAnalysis, Fit, FramingConfig } from "@/lib/framing/types";

const FITS: { value: Fit; label: string; title: string }[] = [
  { value: "cover", label: "Fill", title: "Cover: fills the area, trims the overflow" },
  { value: "contain", label: "Fit", title: "Contain: shows everything, background around it" },
];

type Update = (fn: (c: FramingConfig) => FramingConfig) => void;

/** Re-fit the gameplay source to its (new) panel aspect. */
export function refitGameplay(c: FramingConfig, sourceW: number, sourceH: number): FramingConfig {
  const [, game] = panelAspects(c);
  const fg = c.facecam_gameplay;
  return { ...c, facecam_gameplay: { ...fg, gameplay: roundRect(fitRectToAspect(fg.gameplay, normAspect(game, sourceW, sourceH))) } };
}

export function ModeControls({
  config,
  update,
  sourceW,
  sourceH,
  vision,
  time,
}: {
  config: FramingConfig;
  update: Update;
  sourceW: number;
  sourceH: number;
  vision: ClipVisionAnalysis | null;
  time: number;
}) {
  const outAspect = normAspect(config.output_width / config.output_height, sourceW, sourceH);
  if (config.mode === "manual") {
    const m = config.manual;
    const primary = vision?.tracks.find((t) => t.is_primary) ?? vision?.tracks[0];
    const face = primary ? faceRectAt(primary, time) : null;
    const setCrop = (fn: (r: typeof m.crop) => typeof m.crop) =>
      update((c) => ({ ...c, manual: { ...c.manual, crop: roundRect(fn(c.manual.crop)), keyframes: [] } }));
    return (
      <Panel title="Manual crop">
        <div className="space-y-4">
          {m.keyframes.length > 0 && (
            <p className="rounded-lg bg-info/15 p-2 text-[11px]">
              This crop is animated ({m.keyframes.length} keyframes). Moving it replaces the animation with a fixed crop.
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              variant={m.lock_aspect ? "secondary" : "outline"}
              aria-pressed={m.lock_aspect}
              onClick={() =>
                update((c) => ({
                  ...c,
                  manual: {
                    ...c.manual,
                    lock_aspect: !c.manual.lock_aspect,
                    crop: !c.manual.lock_aspect ? roundRect(fitRectToAspect(c.manual.crop, outAspect)) : c.manual.crop,
                  },
                }))
              }
            >
              {m.lock_aspect ? <Lock /> : <Unlock />} {m.lock_aspect ? "9:16 locked" : "Free aspect"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setCrop((r) => ({ ...r, x: 0.5 - r.w / 2, y: 0.5 - r.h / 2 }))}>
              <Crosshair /> Centre
            </Button>
            {face && (
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  setCrop((r) => ({
                    ...r,
                    x: Math.min(Math.max(face.x + face.w / 2 - r.w / 2, 0), 1 - r.w),
                    y: Math.min(Math.max(face.y + face.h / 2 - r.h / 2, 0), 1 - r.h),
                  }))
                }
              >
                Centre on face
              </Button>
            )}
          </div>
          <LabeledSlider
            label="Zoom"
            value={Math.min(zoomOf(m.crop), 4)}
            min={1}
            max={4}
            step={0.01}
            format={(v) => `${v.toFixed(2)}×`}
            onChange={(z) => setCrop((r) => zoomRect(r, z))}
          />
          {!m.lock_aspect && (
            <div className="space-y-1.5">
              <p className="text-xs font-medium">Fit into 9:16</p>
              <Segmented ariaLabel="Fit" value={m.fit} options={FITS} onChange={(fit) => update((c) => ({ ...c, manual: { ...c.manual, fit } }))} />
            </div>
          )}
          <p className="text-[11px] text-muted-foreground">Arrow keys nudge the selected rectangle (Shift = faster), +/− zoom. Hold Alt while dragging to disable snapping.</p>
        </div>
      </Panel>
    );
  }
  if (config.mode === "facecam_gameplay") {
    const fg = config.facecam_gameplay;
    const setFg = (patch: Partial<typeof fg>, refit = true) =>
      update((c) => {
        const next = { ...c, facecam_gameplay: { ...c.facecam_gameplay, ...patch } };
        return refit ? refitGameplay(next, sourceW, sourceH) : next;
      });
    return (
      <Panel title="Facecam + Gameplay layout">
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Segmented
              ariaLabel="Facecam position"
              value={fg.facecam_position}
              options={[
                { value: "top", label: "Facecam top" },
                { value: "bottom", label: "Facecam bottom" },
              ]}
              onChange={(v) => setFg({ facecam_position: v })}
            />
            <Button size="sm" variant="outline" onClick={() => setFg({ facecam: fg.gameplay, gameplay: fg.facecam })}>
              <ArrowDownUp /> Swap regions
            </Button>
          </div>
          <LabeledSlider label="Facecam panel height" value={fg.split} min={0.15} max={0.7} step={0.01} format={(v) => `${Math.round(v * 100)}%`} onChange={(v) => setFg({ split: v })} />
          <LabeledSlider label="Gap between panels" value={fg.gap} min={0} max={60} step={1} format={(v) => `${Math.round(v)} px`} onChange={(v) => setFg({ gap: v })} />
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <p className="text-xs font-medium">Facecam fit</p>
              <Segmented ariaLabel="Facecam fit" value={fg.facecam_fit} options={FITS} onChange={(v) => setFg({ facecam_fit: v }, false)} />
            </div>
            <div className="space-y-1.5">
              <p className="text-xs font-medium">Gameplay fit</p>
              <Segmented ariaLabel="Gameplay fit" value={fg.gameplay_fit} options={FITS} onChange={(v) => setFg({ gameplay_fit: v }, false)} />
            </div>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Drag the <span className="font-semibold text-ig-pink">Facecam</span> and <span className="font-semibold text-ig-blue">Gameplay</span> regions on the original video. The gameplay region keeps the shape of its panel.
          </p>
        </div>
      </Panel>
    );
  }
  if (config.mode === "pip") {
    const p = config.pip;
    const setPip = (patch: Partial<typeof p>) => update((c) => ({ ...c, pip: { ...c.pip, ...patch } }));
    return (
      <Panel title="Picture-in-Picture">
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <p className="text-xs font-medium">Main video</p>
              <Segmented ariaLabel="Main video fit" value={p.main_fit} options={FITS} onChange={(v) => setPip({ main_fit: v })} />
            </div>
            <div className="space-y-1.5">
              <p className="text-xs font-medium">Overlay</p>
              <Segmented ariaLabel="Overlay fit" value={p.overlay_fit} options={FITS} onChange={(v) => setPip({ overlay_fit: v })} />
            </div>
          </div>
          <LabeledSlider label="Corner radius" value={p.overlay_radius} min={0} max={200} step={1} format={(v) => `${Math.round(v)} px`} onChange={(v) => setPip({ overlay_radius: v })} />
          <LabeledSlider label="Border width" value={p.overlay_border_width} min={0} max={40} step={1} format={(v) => `${Math.round(v)} px`} onChange={(v) => setPip({ overlay_border_width: v })} />
          <ColorField label="Border colour" value={p.overlay_border_color} onChange={(v) => setPip({ overlay_border_color: v })} />
          <ToggleRow label="Drop shadow" checked={p.overlay_shadow} onChange={(v) => setPip({ overlay_shadow: v })} />
          <p className="text-[11px] text-muted-foreground">Move and resize the facecam overlay directly on the 9:16 preview.</p>
        </div>
      </Panel>
    );
  }
  if (config.mode === "full_frame") {
    const ff = config.full_frame;
    return (
      <Panel title="Full frame">
        <div className="space-y-4">
          <LabeledSlider label="Size" value={ff.scale} min={0.5} max={1} step={0.01} format={(v) => `${Math.round(v * 100)}%`} onChange={(v) => update((c) => ({ ...c, full_frame: { ...c.full_frame, scale: v } }))} />
          <LabeledSlider
            label="Vertical position"
            value={ff.vertical_position}
            min={0}
            max={1}
            step={0.01}
            format={(v) => (v < 0.34 ? "Top" : v > 0.66 ? "Bottom" : "Centre") + ` · ${Math.round(v * 100)}%`}
            onChange={(v) => update((c) => ({ ...c, full_frame: { ...c.full_frame, vertical_position: v } }))}
          />
        </div>
      </Panel>
    );
  }
  return null;
}
