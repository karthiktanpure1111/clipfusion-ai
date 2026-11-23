"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { AlertTriangle, Bot, Play, Quote } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { ScoreRing } from "@/components/common/score-ring";
import { ClipStatusBadge, StatusBadge } from "@/components/common/status-badge";
import { DurationPill, VideoThumb } from "@/components/common/video-thumb";
import { useStableUrl } from "@/components/common/use-stable-url";
import type { ClipOut } from "@/lib/api/types";
import { categoryLabel, clipNumber, formatHms } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Hover-to-play proxy preview, looping the clip range; always paused when the card is hidden/unmounted. */
function HoverPreview({ clip, hovered }: { clip: ClipOut; hovered: boolean }) {
  const ref = useRef<HTMLVideoElement>(null);
  const proxy = useStableUrl(clip.proxy_url, `${clip.proxy_start}-${clip.start_time}-${clip.end_time}`);
  const start = Math.max(0, clip.start_time - (clip.proxy_start ?? 0));
  const end = start + clip.duration;

  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    if (hovered) {
      if (Math.abs(v.currentTime - start) > 0.5 && (v.currentTime < start || v.currentTime > end)) v.currentTime = start;
      v.play().catch(() => undefined);
    } else {
      v.pause();
    }
    return () => v.pause();
  }, [hovered, start, end]);

  if (!proxy.url) return null;
  return (
    <video
      ref={ref}
      src={hovered ? proxy.url : undefined}
      onError={proxy.renew}
      muted
      playsInline
      preload="none"
      onLoadedMetadata={(e) => (e.currentTarget.currentTime = start)}
      onTimeUpdate={(e) => {
        if (e.currentTarget.currentTime >= end) e.currentTarget.currentTime = start;
      }}
      className={cn("absolute inset-0 size-full object-cover transition-opacity duration-300", hovered ? "opacity-100" : "opacity-0")}
      aria-hidden
    />
  );
}

export function ClipCard({
  clip,
  view = "grid",
  selected,
  onSelectChange,
}: {
  clip: ClipOut;
  view?: "grid" | "list";
  selected?: boolean;
  onSelectChange?: (v: boolean) => void;
}) {
  const [hovered, setHovered] = useState(false);
  // navigating away hides the card without a mouseleave: don't start playing again on return
  useLayoutEffect(() => () => setHovered(false), []);
  const href = `/app/clips/${clip.id}`;
  const warnings = clip.blocking_warning_count > 0 && !clip.warnings_acknowledged;

  const thumb = (
    <VideoThumb src={clip.thumbnail_url} alt={clip.title || "Clip thumbnail"} className={cn(view === "list" ? "w-44 shrink-0 rounded-xl sm:w-56" : "")}>
      <HoverPreview clip={clip} hovered={hovered} />
      <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-black/20" />
      <div className="absolute top-2 left-2 z-10 flex items-center gap-1.5">
        {onSelectChange && (
          <span className="rounded-md bg-black/60 p-1 backdrop-blur" onClick={(e) => e.stopPropagation()}>
            <Checkbox checked={!!selected} onCheckedChange={(v) => onSelectChange(v === true)} aria-label={`Select ${clip.title || "clip"}`} />
          </span>
        )}
        {warnings && (
          <span className="flex items-center gap-1 rounded-md bg-warning/90 px-1.5 py-0.5 text-[10px] font-semibold text-black">
            <AlertTriangle className="size-3" /> Framing
          </span>
        )}
      </div>
      <div className="absolute right-2 bottom-2 left-2 flex items-end justify-between">
        <DurationPill>
          {formatHms(clip.start_time)}–{formatHms(clip.end_time)}
        </DurationPill>
        <DurationPill>{Math.round(clip.duration)}s</DurationPill>
      </div>
      {clip.proxy_url && !hovered && (
        <span className="absolute top-1/2 left-1/2 grid size-10 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-black/50 opacity-0 backdrop-blur transition-opacity group-hover:opacity-100">
          <Play className="size-4 fill-white text-white" />
        </span>
      )}
    </VideoThumb>
  );

  return (
    <motion.article
      whileHover={{ y: -3 }}
      transition={{ type: "spring", stiffness: 400, damping: 30 }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setHovered(true)}
      onBlur={() => setHovered(false)}
      className={cn(
        "group relative overflow-hidden rounded-2xl border bg-card/70 transition-colors",
        selected ? "border-ig-pink/50 ring-1 ring-ig-pink/40" : "border-white/7 hover:border-white/15",
        view === "list" && "flex gap-4 p-3",
      )}
    >
      {thumb}
      <div className={cn("flex min-w-0 flex-1 flex-col", view === "grid" ? "p-4" : "py-1 pr-1")}>
        <div className="flex items-start gap-3">
          <div className="min-w-0 flex-1">
            <Link
              href={href}
              className="line-clamp-2 font-medium leading-snug outline-none after:absolute after:inset-0 after:rounded-2xl focus-visible:after:ring-2 focus-visible:after:ring-ring"
            >
              {clip.title || clip.hook_text || "Untitled clip"}
            </Link>
            <p className="mt-0.5 truncate text-xs text-muted-foreground">
              {clip.sequence ? <span className="font-semibold text-foreground/80">{clipNumber(clip.sequence)} · </span> : null}
              {clip.source_title}
            </p>
          </div>
          <div className="relative z-10">
            <ScoreRing score={clip.highlight_score} size={40} />
          </div>
        </div>
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
          <span className="rounded-full bg-white/6 px-2 py-0.5 text-[11px] text-foreground/85">{categoryLabel(clip.category)}</span>
          <ClipStatusBadge clip={clip} />
          {clip.render_status === "stale" || clip.render_status === "failed" ? <StatusBadge status={clip.render_status} /> : null}
          {clip.detection_method === "signals+llm" && (
            <span className="flex items-center gap-1 text-[11px] text-muted-foreground" title="Ranked with an LLM">
              <Bot className="size-3" /> LLM
            </span>
          )}
        </div>
        <p className="mt-1.5 text-[10px] text-muted-foreground/70">AI highlight score (estimate)</p>
        {clip.reasons.length > 0 && (
          <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">
            {clip.reasons.slice(0, view === "list" ? 3 : 2).map((r) => (
              <li key={r} className="line-clamp-1">• {r}</li>
            ))}
          </ul>
        )}
        {clip.transcript_text && (
          <p className="mt-2 flex gap-1.5 text-xs text-foreground/70 italic">
            <Quote className="mt-0.5 size-3 shrink-0 opacity-50" />
            <span className="line-clamp-2">{clip.transcript_text}</span>
          </p>
        )}
      </div>
    </motion.article>
  );
}
