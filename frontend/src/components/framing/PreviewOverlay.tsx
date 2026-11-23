"use client";

/**
 * Direct manipulation on the 9:16 output preview: the PiP overlay destination (drag/resize;
 * its source region is re-fitted to the new shape on release) and the facecam/gameplay split.
 */
import { useRef, type PointerEvent } from "react";

import { fitRectToAspect, normAspect, roundRect } from "@/lib/framing/editing";
import { panelDests } from "@/lib/framing/layout";
import type { FramingConfig } from "@/lib/framing/types";
import { refitGameplay } from "./ModeControls";
import { RectEditor } from "./RectEditor";
import { FACECAM_COLOR } from "./SourcePanel";

type Update = (fn: (c: FramingConfig) => FramingConfig) => void;

export function PreviewOverlay({ config, update, sourceW, sourceH }: { config: FramingConfig; update: Update; sourceW: number; sourceH: number }) {
  if (config.mode === "pip") {
    return (
      <RectEditor
        rect={config.pip.overlay_dest}
        color={FACECAM_COLOR}
        label="Facecam overlay"
        snapTargets={[]}
        onChange={(r) => update((c) => ({ ...c, pip: { ...c.pip, overlay_dest: roundRect(r) } }))}
        onCommit={(r) =>
          update((c) => {
            const pixelAspect = (r.w * c.output_width) / (r.h * c.output_height);
            const src = fitRectToAspect(c.pip.overlay_source, normAspect(pixelAspect, sourceW, sourceH));
            return { ...c, pip: { ...c.pip, overlay_source: roundRect(src) } };
          })
        }
      />
    );
  }
  if (config.mode === "facecam_gameplay") return <SplitHandle config={config} update={update} sourceW={sourceW} sourceH={sourceH} />;
  return null;
}

function SplitHandle({ config, update, sourceW, sourceH }: { config: FramingConfig; update: Update; sourceW: number; sourceH: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const fg = config.facecam_gameplay;
  const [face] = panelDests(fg, config.output_height);
  const y = fg.facecam_position === "top" ? face.y + face.h : face.y;
  const setSplit = (split: number) =>
    update((c) =>
      refitGameplay({ ...c, facecam_gameplay: { ...c.facecam_gameplay, split: Math.round(Math.min(Math.max(split, 0.15), 0.7) * 1000) / 1000 } }, sourceW, sourceH),
    );
  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    const parent = ref.current?.parentElement?.getBoundingClientRect();
    if (!dragging.current || !parent) return;
    const ny = (e.clientY - parent.top) / parent.height;
    setSplit(fg.facecam_position === "top" ? ny : 1 - ny);
  };
  return (
    <div
      ref={ref}
      role="slider"
      tabIndex={0}
      aria-label="Facecam panel height"
      aria-valuemin={15}
      aria-valuemax={70}
      aria-valuenow={Math.round(fg.split * 100)}
      onKeyDown={(e) => {
        const dir = e.key === "ArrowUp" ? -1 : e.key === "ArrowDown" ? 1 : 0;
        if (!dir) return;
        e.preventDefault();
        setSplit(fg.split + (fg.facecam_position === "top" ? dir : -dir) * 0.01);
      }}
      onPointerDown={(e) => {
        dragging.current = true;
        e.currentTarget.setPointerCapture(e.pointerId);
      }}
      onPointerMove={onMove}
      onPointerUp={() => {
        dragging.current = false;
      }}
      className="group absolute inset-x-0 z-10 -my-2 flex h-4 cursor-ns-resize touch-none items-center outline-none"
      style={{ top: `${y * 100}%` }}
    >
      <span className="h-0.5 w-full bg-white/70 shadow group-hover:bg-white group-focus-visible:bg-ig-pink" />
      <span className="bg-ig-gradient absolute left-1/2 -translate-x-1/2 rounded-full px-2 py-0.5 text-[9px] font-semibold text-white shadow">
        {Math.round(fg.split * 100)}% facecam
      </span>
    </div>
  );
}
