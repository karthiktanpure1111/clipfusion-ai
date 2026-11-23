"use client";

/**
 * Draggable + resizable normalised rectangle (8 handles) placed inside a positioned parent.
 * Drag to move (snaps to the centre / faces; hold Alt to disable snapping), handles resize
 * (aspect-locked when ``aspect`` is set), arrow keys nudge (Shift = x4), +/- zoom around the
 * centre. ``onChange`` fires live, ``onCommit`` once per gesture.
 */
import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { cn } from "@/lib/utils";

import { HANDLES, moveRect, resizeRect, snapRect, zoomOf, zoomRect, type Handle } from "@/lib/framing/editing";
import type { Rect } from "@/lib/framing/types";

const CURSOR: Record<Handle, string> = {
  n: "cursor-ns-resize",
  s: "cursor-ns-resize",
  e: "cursor-ew-resize",
  w: "cursor-ew-resize",
  ne: "cursor-nesw-resize",
  sw: "cursor-nesw-resize",
  nw: "cursor-nwse-resize",
  se: "cursor-nwse-resize",
};
const POS: Record<Handle, string> = {
  nw: "-top-1.5 -left-1.5",
  n: "-top-1.5 left-1/2 -translate-x-1/2",
  ne: "-top-1.5 -right-1.5",
  e: "top-1/2 -right-1.5 -translate-y-1/2",
  se: "-bottom-1.5 -right-1.5",
  s: "-bottom-1.5 left-1/2 -translate-x-1/2",
  sw: "-bottom-1.5 -left-1.5",
  w: "top-1/2 -left-1.5 -translate-y-1/2",
};

export interface RectEditorProps {
  rect: Rect;
  onChange: (r: Rect) => void;
  onCommit?: (r: Rect) => void;
  aspect?: number | null;
  color: string;
  label: string;
  description?: string;
  snapTargets?: { x: number; y: number }[];
  dashed?: boolean;
  className?: string;
}

interface Drag {
  mode: "move" | Handle;
  sx: number;
  sy: number;
  start: Rect;
  pw: number;
  ph: number;
  last: Rect;
}

export function RectEditor({ rect, onChange, onCommit, aspect = null, color, label, description, snapTargets = [], dashed, className }: RectEditorProps) {
  const ref = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const [guides, setGuides] = useState<{ x: number[]; y: number[] }>({ x: [], y: [] });
  const [active, setActive] = useState(false);

  const begin = (mode: Drag["mode"], e: PointerEvent<HTMLElement>) => {
    if (e.button !== 0) return;
    const parent = ref.current?.parentElement?.getBoundingClientRect();
    if (!parent) return;
    e.stopPropagation();
    e.preventDefault();
    ref.current?.focus();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { mode, sx: e.clientX, sy: e.clientY, start: rect, pw: parent.width, ph: parent.height, last: rect };
    setActive(true);
  };

  const move = (e: PointerEvent<HTMLElement>) => {
    const d = drag.current;
    if (!d) return;
    const dx = (e.clientX - d.sx) / d.pw;
    const dy = (e.clientY - d.sy) / d.ph;
    let next: Rect;
    if (d.mode === "move") {
      next = moveRect(d.start, dx, dy);
      if (!e.altKey) {
        const s = snapRect(next, snapTargets);
        next = s.rect;
        setGuides({ x: s.guidesX, y: s.guidesY });
      } else setGuides({ x: [], y: [] });
    } else next = resizeRect(d.start, d.mode, dx, dy, aspect);
    d.last = next;
    onChange(next);
  };

  const end = (e: PointerEvent<HTMLElement>) => {
    const d = drag.current;
    if (!d) return;
    drag.current = null;
    setActive(false);
    setGuides({ x: [], y: [] });
    (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
    onCommit?.(d.last);
  };

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 0.02 : 0.005;
    let next: Rect | null = null;
    if (e.key === "ArrowLeft") next = moveRect(rect, -step, 0);
    else if (e.key === "ArrowRight") next = moveRect(rect, step, 0);
    else if (e.key === "ArrowUp") next = moveRect(rect, 0, -step);
    else if (e.key === "ArrowDown") next = moveRect(rect, 0, step);
    else if (e.key === "+" || e.key === "=") next = zoomRect(rect, zoomOf(rect) * 1.05);
    else if (e.key === "-" || e.key === "_") next = zoomRect(rect, Math.max(1, zoomOf(rect) / 1.05));
    if (!next) return;
    e.preventDefault();
    e.stopPropagation();
    onChange(next);
    onCommit?.(next);
  };

  return (
    <>
      <div
        ref={ref}
        role="group"
        tabIndex={0}
        aria-label={`${label}: drag to move, handles to resize, arrow keys to nudge, plus/minus to zoom`}
        title={description}
        onPointerDown={(e) => begin("move", e)}
        onPointerMove={move}
        onPointerUp={end}
        onPointerCancel={end}
        onKeyDown={onKey}
        className={cn(
          "group absolute cursor-move touch-none outline-none select-none focus-visible:ring-2 focus-visible:ring-white/80",
          active && "z-20",
          className,
        )}
        style={{
          left: `${rect.x * 100}%`,
          top: `${rect.y * 100}%`,
          width: `${rect.w * 100}%`,
          height: `${rect.h * 100}%`,
          border: `2px ${dashed ? "dashed" : "solid"} ${color}`,
          boxShadow: `0 0 0 1px rgba(0,0,0,.45), 0 0 18px ${color}55`,
        }}
      >
        <span
          className="pointer-events-none absolute -top-6 left-0 rounded-md px-1.5 py-0.5 text-[10px] font-semibold whitespace-nowrap text-white shadow"
          style={{ background: color }}
        >
          {label}
        </span>
        <span className="pointer-events-none absolute inset-0 m-auto grid size-4 place-items-center opacity-60">
          <span className="absolute h-px w-4" style={{ background: color }} />
          <span className="absolute h-4 w-px" style={{ background: color }} />
        </span>
        {HANDLES.map((h) => (
          <span
            key={h}
            aria-hidden
            onPointerDown={(e) => begin(h, e)}
            onPointerMove={move}
            onPointerUp={end}
            onPointerCancel={end}
            className={cn("absolute z-10 size-3 rounded-[3px] border-2 bg-white shadow", POS[h], CURSOR[h])}
            style={{ borderColor: color }}
          />
        ))}
      </div>
      {guides.x.map((x) => (
        <span key={`gx${x}`} aria-hidden className="pointer-events-none absolute inset-y-0 w-px bg-ig-orange/90" style={{ left: `${x * 100}%` }} />
      ))}
      {guides.y.map((y) => (
        <span key={`gy${y}`} aria-hidden className="pointer-events-none absolute inset-x-0 h-px bg-ig-orange/90" style={{ top: `${y * 100}%` }} />
      ))}
    </>
  );
}
