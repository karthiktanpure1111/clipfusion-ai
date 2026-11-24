"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { CalendarClock, ChevronDown, Clapperboard, Crop, ExternalLink, Loader2, Pencil, Play, Trash2, UploadCloud, X } from "lucide-react";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { ClipStatusBadge, clipStatusOf } from "@/components/common/status-badge";
import { DurationPill, VideoThumb } from "@/components/common/video-thumb";
import { useStableUrl } from "@/components/common/use-stable-url";
import { RemoveClipDialog } from "@/components/clips/remove-clip-dialog";
import { SingleUploadDialog } from "@/components/uploads/upload-controls";
import { inText, slotText } from "@/components/uploads/schedule-panel";
import { useNow } from "@/components/common/use-now";
import { useUserTz } from "@/lib/api/hooks/auth";
import { useScheduledItemAction } from "@/lib/api/hooks/timeline";
import { ApiError } from "@/lib/api/client";
import { toastError } from "@/lib/api/hooks/common";
import { useClipMeta, useRenderClip } from "@/lib/api/hooks/clips";
import type { ClipMetadata, ClipOut } from "@/lib/api/types";
import { CaptionEditor } from "./caption-editor";
import { browserTimezone, clipNumber, formatBytes, formatDuration, formatHms } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Inline player: the rendered MP4 when there is one, else the source proxy looped over the clip range. */
function InlinePreview({ clip, onClose }: { clip: ClipOut; onClose: () => void }) {
  const ref = useRef<HTMLVideoElement>(null);
  const rendered = !!clip.output_url;
  const media = useStableUrl(
    rendered ? clip.output_url : clip.proxy_url,
    rendered ? (clip.rendered_at ?? "") : `${clip.proxy_start}-${clip.start_time}-${clip.end_time}`,
  );
  const start = rendered ? 0 : Math.max(0, clip.start_time - (clip.proxy_start ?? 0));
  const end = rendered ? Infinity : start + clip.duration;
  useEffect(() => {
    const v = ref.current;
    return () => v?.pause(); // pause when hidden by <Activity> or unmounted
  }, []);
  return (
    <div className={cn("relative overflow-hidden rounded-xl bg-black", rendered ? "aspect-[9/16] w-36 sm:w-40" : "aspect-video w-full sm:w-64")}>
      <video
        ref={ref}
        src={media.url}
        onError={media.renew}
        controls
        autoPlay
        playsInline
        preload="metadata"
        onLoadedMetadata={(e) => {
          if (!rendered) e.currentTarget.currentTime = start;
        }}
        onTimeUpdate={(e) => {
          const v = e.currentTarget;
          if (v.currentTime >= end && !v.paused) {
            v.pause();
            v.currentTime = start;
          }
        }}
        className="size-full object-contain"
        aria-label={rendered ? "Rendered clip" : "Source preview of the clip range"}
      />
      <button
        type="button"
        onClick={onClose}
        aria-label="Close preview"
        className="absolute top-1.5 right-1.5 grid size-7 place-items-center rounded-full bg-black/70 text-white hover:bg-black focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <X className="size-4" />
      </button>
      {!rendered && <span className="absolute bottom-1.5 left-1.5 rounded bg-black/70 px-1.5 text-[10px] text-white/80">Source preview</span>}
    </div>
  );
}

export function ClipTimelineRow({ clip }: { clip: ClipOut }) {
  const [playing, setPlaying] = useState(false);
  const [transcriptOpen, setTranscriptOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [removeOpen, setRemoveOpen] = useState(false);
  const meta = useClipMeta(clip);
  const tz = useUserTz() ?? browserTimezone();
  const now = useNow();
  const scheduleAction = useScheduledItemAction();
  const sm = clip.social_metadata ?? meta.data?.social_metadata;
  const status = clipStatusOf(clip);
  const cleaned = status === "cleaned";
  const hasMedia = clip.media_available !== false && !cleaned;
  const canPreview = hasMedia && (!!clip.output_url || !!clip.proxy_url);
  const canUpload = status === "generated" || status === "upload_failed";
  const render = useRenderClip(clip.id);
  const rendering = clip.render_status === "queued" || clip.render_status === "rendering" || render.isPending;
  const canRender = status === "pending" && hasMedia && !clip.needs_framing_review;
  const startRender = async () => {
    try {
      await render.mutateAsync(false);
      toast.success(`Rendering ${clipNumber(clip.sequence)}`, {
        description: "When it's done it goes to YouTube: right away, or 1 hour after the previous clip.",
      });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.code === "framing_warnings_unacknowledged")
        toast.warning("Check the framing first", {
          description: "This framing would hide or cut a face. Open the framing editor, fix or accept it, then render.",
        });
      else toastError(e, "Could not start the render");
    }
  };
  const title = sm?.youtube.title || clip.title || clip.hook_text || "Untitled clip";
  const caption = sm?.instagram.caption && sm.instagram.caption !== title ? sm.instagram.caption : null;
  const description = sm?.youtube.description;
  const hashtags = sm?.youtube.hashtags?.length ? sm.youtube.hashtags : (sm?.instagram.hashtags ?? []);

  return (
    <li className="flex flex-col gap-4 rounded-2xl border border-white/7 bg-card/70 p-3 sm:flex-row sm:p-4">
      <div className="shrink-0">
        {playing && canPreview ? (
          <InlinePreview clip={clip} onClose={() => setPlaying(false)} />
        ) : (
          <VideoThumb src={clip.thumbnail_url} alt={title} className="w-full rounded-xl sm:w-64">
            <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-transparent to-transparent" />
            <DurationPill className="absolute bottom-2 left-2">{formatDuration(clip.duration)}</DurationPill>
            {canPreview && (
              <button
                type="button"
                onClick={() => setPlaying(true)}
                aria-label={`Play ${clipNumber(clip.sequence)}`}
                className="absolute top-1/2 left-1/2 grid size-11 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full bg-black/55 backdrop-blur transition hover:scale-105 hover:bg-black/75 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                <Play className="size-5 fill-white text-white" />
              </button>
            )}
          </VideoThumb>
        )}
      </div>

      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span className="font-heading text-sm font-bold">{clipNumber(clip.sequence)}</span>
          <span className="font-mono text-xs text-muted-foreground tabular-nums" title="Position in the original video">
            {formatHms(clip.start_time)} – {formatHms(clip.end_time)}
          </span>
          <span className="text-xs text-muted-foreground">{formatDuration(clip.duration)}</span>
          <ClipStatusBadge clip={clip} />
          {clip.scheduled_at && (
            <span
              className="inline-flex items-center gap-1 rounded-full bg-info/10 px-2 py-0.5 text-xs text-info ring-1 ring-info/25"
              title={`Uploads to YouTube ${inText(clip.scheduled_at, now)}`}
            >
              <CalendarClock className="size-3" /> Scheduled {slotText(clip.scheduled_at, tz)}
            </span>
          )}
          <span className="text-xs text-muted-foreground" title="AI highlight score — an estimate of how engaging the moment is, not a prediction of views">
            AI score <span className="font-semibold text-foreground tabular-nums">{Math.round(clip.highlight_score)}</span> (estimate)
          </span>
        </div>

        <CaptionEditor
          clip={clip}
          caption={title}
          meta={sm as ClipMetadata | undefined}
          locked={status === "uploaded" || status === "cleaned" || status === "uploading"}
        />
        {caption && <p className="line-clamp-2 text-sm text-foreground/80">{caption}</p>}
        {description ? (
          <p className="line-clamp-3 text-sm whitespace-pre-line text-muted-foreground">{description}</p>
        ) : (
          !sm && meta.isLoading && <p className="text-xs text-muted-foreground">Loading description…</p>
        )}
        {hashtags.length > 0 && (
          <p className="flex flex-wrap gap-x-2 text-xs text-info">
            {hashtags.map((h) => (
              <span key={h}>#{h.replace(/^#/, "")}</span>
            ))}
          </p>
        )}

        <div>
          <button
            type="button"
            aria-expanded={transcriptOpen}
            onClick={() => setTranscriptOpen((o) => !o)}
            className="flex items-center gap-1 rounded text-xs text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            <ChevronDown className={cn("size-3.5 transition-transform", transcriptOpen && "rotate-180")} /> Transcript
          </button>
          {transcriptOpen && (
            <p className="mt-1.5 rounded-lg bg-black/20 p-2.5 text-sm leading-relaxed whitespace-pre-wrap text-foreground/85">
              {clip.transcript_text || "No speech was transcribed in this range."}
            </p>
          )}
        </div>

        {cleaned && (
          <p className="text-xs text-success">
            Uploaded — local files deleted
            {clip.freed_bytes ? ` (freed ${formatBytes(clip.freed_bytes)})` : ""}
          </p>
        )}
        {clip.youtube_url && (
          <a href={clip.youtube_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-info hover:underline">
            <ExternalLink className="size-3.5" /> Watch on YouTube
          </a>
        )}
        {status === "upload_failed" && clip.upload_error && <p className="text-xs break-words text-[#ff8098]">Upload failed: {clip.upload_error}</p>}

        <div className="flex flex-wrap gap-2 pt-1">
          {(canRender || rendering) && status !== "generated" && (
            <Button size="sm" className="glow-red rounded-lg" onClick={startRender} disabled={rendering}>
              {rendering ? <Loader2 className="animate-spin" /> : <Clapperboard />} {rendering ? "Rendering…" : "Render"}
            </Button>
          )}
          {status === "pending" && clip.needs_framing_review && (
            <Link href={`/app/clips/${clip.id}/framing`} className={cn(buttonVariants({ size: "sm" }), "rounded-lg")}>
              <Crop /> Review framing to render
            </Link>
          )}
          {canUpload && (
            <Button size="sm" className="rounded-lg" onClick={() => setUploadOpen(true)}>
              <UploadCloud /> {clip.scheduled_at ? "Upload now" : "Upload"}
            </Button>
          )}
          {clip.scheduled_item_id && (
            <Button
              size="sm"
              variant="outline"
              className="rounded-lg"
              disabled={scheduleAction.isPending}
              onClick={() =>
                scheduleAction.mutate(
                  { id: clip.scheduled_item_id as string, action: "cancel" },
                  {
                    onSuccess: () => toast.info(`${clipNumber(clip.sequence)} unscheduled`),
                  },
                )
              }
            >
              <X /> Unschedule
            </Button>
          )}
          <Link href={`/app/clips/${clip.id}`} className={cn(buttonVariants({ variant: "outline", size: "sm" }), "rounded-lg")}>
            <Pencil /> Edit
          </Link>
          {hasMedia && (
            <Link href={`/app/clips/${clip.id}/framing`} className={cn(buttonVariants({ variant: "outline", size: "sm" }), "rounded-lg")}>
              <Crop /> Framing editor
            </Link>
          )}
          <Button
            size="sm"
            variant="ghost"
            className="rounded-lg text-muted-foreground hover:text-[#ff5c7a]"
            onClick={() => setRemoveOpen(true)}
            disabled={status === "uploading"}
          >
            <Trash2 /> Remove Clip
          </Button>
        </div>
      </div>
      {canUpload && <SingleUploadDialog clip={clip} open={uploadOpen} onOpenChange={setUploadOpen} />}
      <RemoveClipDialog clip={clip} open={removeOpen} onOpenChange={setRemoveOpen} />
    </li>
  );
}
