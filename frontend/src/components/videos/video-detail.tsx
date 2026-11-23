"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { AlertTriangle, ArrowLeft, FileText, FileVideo, Info, Sparkles, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { ErrorState, SectionCard, Surface } from "@/components/common/page";
import { StatusBadge } from "@/components/common/status-badge";
import { VideoThumb } from "@/components/common/video-thumb";
import { useStableUrl } from "@/components/common/use-stable-url";
import { JobCard } from "@/components/processing/job-card";
import { UploadManager } from "@/components/uploads/upload-manager";
import { useMe, useUserTz } from "@/lib/api/hooks/auth";
import { useClips } from "@/lib/api/hooks/clips";
import { useUploadRuns } from "@/lib/api/hooks/uploads";
import { isSourceBusy, useDeleteOriginal, useDeleteSource, useMediaUrl, usePipeline, useSource } from "@/lib/api/hooks/sources";
import type { ClipOut, SourceOut } from "@/lib/api/types";
import { formatBytes, formatDateTime, formatDuration, titleCase } from "@/lib/format";
import { AnalyzeDialog } from "./analyze-dialog";
import { ClipTimeline } from "./clip-timeline";
import { PipelineDashboard } from "./pipeline-dashboard";
import { defaultAnalysis } from "./processing-options";
import { RetentionChart } from "./retention-chart";
import { AttachMediaPanel, canAnalyze, NoYouTubeDownloadNote, YouTubeMetaCard } from "./source-panels";
import { TranscriptViewer } from "./transcript-viewer";

const originalGone = (s: SourceOut) => s.original_available === false;

function Player({ source, videoRef, onTime }: { source: SourceOut; videoRef: React.RefObject<HTMLVideoElement | null>; onTime: (t: number) => void }) {
  const playable = source.has_file && !originalGone(source);
  const media = useMediaUrl(source.id, playable);
  const file = useStableUrl(media.data?.url);
  const poster = useStableUrl(source.thumbnail_url);
  useEffect(() => {
    const v = videoRef.current;
    return () => v?.pause(); // pause when the route is hidden by <Activity> or unmounted
  }, [videoRef, file.url]);

  if (playable && media.data?.browser_playable && file.url) {
    return (
      <video
        ref={videoRef}
        src={file.url}
        poster={poster.url}
        onError={file.renew}
        controls
        playsInline
        preload="metadata"
        onTimeUpdate={(e) => onTime(e.currentTarget.currentTime)}
        className="aspect-video w-full rounded-2xl bg-black"
      />
    );
  }
  return (
    <VideoThumb src={source.thumbnail_url} alt="" className="rounded-2xl">
      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 to-transparent p-4 text-xs text-white/80">
        {originalGone(source)
          ? "The original video was deleted to free space. Clips, transcripts and YouTube links are kept."
          : !source.has_file
            ? "No video file yet — download it from YouTube or upload the creator-provided file to start processing."
            : media.isLoading
              ? "Loading player…"
              : "This file format can't be played in the browser directly. Clip previews use generated proxies."}
      </div>
    </VideoThumb>
  );
}

function Meta({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] tracking-wide text-muted-foreground uppercase">{label}</dt>
      <dd className="mt-0.5 truncate text-sm">{value ?? "–"}</dd>
    </div>
  );
}

/** Every clip is uploaded (or cleaned) or removed — the original is no longer needed. */
function allClipsDone(clips: ClipOut[], removed: number) {
  if (!clips.length) return removed > 0;
  return clips.every((c) => c.status === "uploaded" || c.status === "cleaned");
}

function Detail({ id }: { id: string }) {
  const router = useRouter();
  const tz = useUserTz();
  const me = useMe();
  const { data: source, isLoading, error, refetch } = useSource(id);
  const pipeline = usePipeline(id, !!source && isSourceBusy(source));
  const runs = useUploadRuns(id);
  const live = pipeline.data?.status === "running" || !!runs.data?.some((r) => r.status === "running");
  const clips = useClips({ video_source_id: id, sort: "sequence", limit: 100 }, { pollMs: live ? 3000 : false });
  const del = useDeleteSource();
  const delOriginal = useDeleteOriginal();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [time, setTime] = useState(0);
  const [analyzeOpen, setAnalyzeOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [confirmOriginal, setConfirmOriginal] = useState(false);

  // when processing finishes, refresh the source (status, transcript, clip count)
  const pipeStatus = pipeline.data?.status;
  const prevStatus = useRef(pipeStatus);
  useEffect(() => {
    if (prevStatus.current === "running" && pipeStatus && pipeStatus !== "running") void refetch();
    prevStatus.current = pipeStatus;
  }, [pipeStatus, refetch]);

  if (error) return <ErrorState error={error} onRetry={() => refetch()} />;
  if (isLoading || !source)
    return (
      <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
        <Skeleton className="aspect-video rounded-2xl" />
        <Skeleton className="h-80 rounded-2xl" />
      </div>
    );

  const seek = (t: number) => {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = t;
    v.play().catch(() => undefined);
  };
  const analysed = source.status === "ready" || !!source.processed_at;
  const clipItems = clips.data?.items ?? [];
  const done = allClipsDone(clipItems, pipeline.data?.clips.removed ?? 0);
  const canDeleteOriginal = source.has_file && !originalGone(source) && done;
  const showPipeline = !!pipeline.data && !(pipeline.data.status === "idle" && !pipeline.data.started_at);

  return (
    <>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <Link href="/app/videos" className="mb-2 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            <ArrowLeft className="size-3.5" /> Videos
          </Link>
          <h1 className="font-heading text-2xl font-bold tracking-tight">{source.title || "Untitled video"}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <StatusBadge status={source.status} />
            <span>
              {titleCase(source.source_type)} · added {formatDateTime(source.created_at, tz)}
              {source.processing_mode ? ` · ${titleCase(source.processing_mode)} mode` : ""}
            </span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="lg" className="glow-red rounded-xl" disabled={!canAnalyze(source) || originalGone(source)} onClick={() => setAnalyzeOpen(true)}>
            <Sparkles /> {analysed ? "Re-process" : "Process"}
          </Button>
          <Button variant="outline" size="lg" className="rounded-xl" onClick={() => setConfirmDelete(true)}>
            <Trash2 /> Delete
          </Button>
        </div>
      </div>

      {showPipeline ? (
        <div className="mb-6">
          <PipelineDashboard pipeline={pipeline.data} />
        </div>
      ) : pipeline.error && source.active_job ? (
        <div className="mb-6">
          <JobCard job={source.active_job} />
        </div>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
        <div className="min-w-0 space-y-6">
          <Player source={source} videoRef={videoRef} onTime={setTime} />
          {source.error && (
            <div role="alert" className="flex gap-2 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-[#ff5c7a]" /> {source.error}
            </div>
          )}
          {source.warnings.length > 0 && (
            <SectionCard title="Processing notes" icon={Info}>
              <ul className="space-y-1.5 text-sm text-muted-foreground">
                {source.warnings.map((w) => (
                  <li key={w} className="flex gap-2">
                    <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" /> {w}
                  </li>
                ))}
              </ul>
            </SectionCard>
          )}
          <ClipTimeline
            clips={clipItems}
            loading={clips.isLoading}
            emptyText={
              pipeline.data?.status === "running"
                ? "Clips appear here as soon as they are generated."
                : canAnalyze(source)
                  ? "Process this video to generate clips."
                  : "Clips appear here after processing."
            }
          />
        </div>

        <div className="min-w-0 space-y-6">
          {!source.has_file && (
            <SectionCard title="Add the video file" description="Processing needs the video itself." icon={FileVideo}>
              <div className="space-y-4">
                {source.source_type === "youtube" && <NoYouTubeDownloadNote />}
                <AttachMediaPanel source={source} analysis={defaultAnalysis(me.data?.settings)} onAttached={() => refetch()} />
              </div>
            </SectionCard>
          )}
          <UploadManager sourceId={source.id} clips={clipItems} />
          <Surface className="p-5">
            <dl className="grid grid-cols-2 gap-4">
              <Meta label="Duration" value={formatDuration(source.duration)} />
              <Meta label="Resolution" value={source.width ? `${source.width}×${source.height}` : null} />
              <Meta label="Frame rate" value={source.fps ? `${source.fps.toFixed(2).replace(/\.00$/, "")} fps` : null} />
              <Meta label="Audio" value={source.has_audio == null ? null : source.has_audio ? "Yes" : "No audio track"} />
              <Meta label="Clips" value={source.clip_count} />
              <Meta label="Language" value={source.transcript_language} />
              <Meta label="Original file" value={originalGone(source) ? "Deleted" : formatBytes(source.original_bytes ?? source.file_size)} />
              <Meta label="Rights confirmed" value={source.rights_confirmed_at ? formatDateTime(source.rights_confirmed_at, tz) : "No"} />
            </dl>
            {source.attribution && <p className="mt-4 text-xs text-muted-foreground">Credit: {source.attribution.replace(/^credit:\s*/i, "")}</p>}
            {canDeleteOriginal && (
              <div className="mt-4 rounded-xl border border-white/8 bg-black/15 p-3">
                <p className="text-sm font-medium">All clips are uploaded or removed</p>
                <p className="mt-0.5 text-xs text-muted-foreground">You can delete the original video to free {formatBytes(source.original_bytes ?? source.file_size)}.</p>
                <Button size="sm" variant="outline" className="mt-2 rounded-lg" onClick={() => setConfirmOriginal(true)}>
                  <Trash2 /> Delete original
                </Button>
              </div>
            )}
          </Surface>
          {source.source_type === "youtube" && <YouTubeMetaCard source={source} />}
          <SectionCard title="Transcript" icon={FileText}>
            <TranscriptViewer sourceId={source.id} enabled={analysed} currentTime={time} onSeek={source.has_file && !originalGone(source) ? seek : undefined} />
          </SectionCard>
          {source.retention_points?.length ? <RetentionChart sourceId={source.id} points={source.retention_points} duration={source.duration} /> : null}
        </div>
      </div>

      <AnalyzeDialog source={source} open={analyzeOpen} onOpenChange={setAnalyzeOpen} />
      <ConfirmDialog
        open={confirmOriginal}
        onOpenChange={setConfirmOriginal}
        title="Delete the original video?"
        description="Deletes only the original long video file and its preview proxies from this computer. Clip records, transcripts and YouTube links stay. You won't be able to re-process this video without adding the file again."
        confirmLabel="Delete original"
        destructive
        pending={delOriginal.isPending}
        onConfirm={() =>
          delOriginal.mutate(source.id, {
            onSuccess: (r) => {
              toast.success("Original video deleted", r?.freed_bytes ? { description: `Freed ${formatBytes(r.freed_bytes)}` } : undefined);
              setConfirmOriginal(false);
            },
          })
        }
      />
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete this video?"
        description="The video, its transcript and every clip generated from it are permanently deleted from ClipFusion. Videos already uploaded to YouTube stay on YouTube."
        confirmLabel="Delete video"
        destructive
        pending={del.isPending}
        onConfirm={() =>
          del.mutate(source.id, {
            onSuccess: () => {
              setConfirmDelete(false);
              router.push("/app/videos");
            },
          })
        }
      />
    </>
  );
}

export function VideoDetailView() {
  const { id } = useParams<{ id: string }>();
  return <Detail key={id} id={id} />;
}
