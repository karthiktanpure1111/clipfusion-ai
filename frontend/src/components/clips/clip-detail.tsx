"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Crop, ExternalLink, Quote, Send, Trash2, UploadCloud } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState, SectionCard } from "@/components/common/page";
import { ClipStatusBadge, clipStatusOf, StatusBadge } from "@/components/common/status-badge";
import { JobCard } from "@/components/processing/job-card";
import { QueueItemRow } from "@/components/calendar/queue-item";
import { SingleUploadDialog } from "@/components/uploads/upload-controls";
import { useClip } from "@/lib/api/hooks/clips";
import { useQueue } from "@/lib/api/hooks/publishing";
import { categoryLabel, clipNumber, formatBytes, formatHms } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ClipPlayer } from "./clip-player";
import { MetadataEditor } from "./metadata-editor";
import { PublishDialog } from "./publish-dialog";
import { RemoveClipDialog } from "./remove-clip-dialog";
import { RenderPanel } from "./render-panel";
import { ScorePanel, TrimEditor } from "./trim-and-score";

function QueueList({ clipId }: { clipId: string }) {
  const { data, isLoading } = useQueue({ clip_id: clipId });
  if (isLoading) return <Skeleton className="h-20 rounded-xl" />;
  if (!data?.length) return <p className="text-sm text-muted-foreground">Not scheduled anywhere yet.</p>;
  return (
    <ul className="space-y-2">
      {data.map((q) => (
        <QueueItemRow key={q.id} item={q} />
      ))}
    </ul>
  );
}

function Detail({ id }: { id: string }) {
  const router = useRouter();
  const { data: clip, error, isLoading, refetch } = useClip(id);
  const [publishOpen, setPublishOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);

  if (error) return <ErrorState error={error} onRetry={() => refetch()} />;
  if (isLoading || !clip)
    return (
      <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
        <Skeleton className="aspect-video rounded-2xl" />
        <Skeleton className="h-96 rounded-2xl" />
      </div>
    );

  const status = clipStatusOf(clip);
  const canUpload = status === "generated" || status === "upload_failed";
  const hasMedia = clip.media_available !== false && status !== "cleaned";

  return (
    <>
      <header className="mb-6 flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
        <div className="min-w-0">
          <Link href={`/app/videos/${clip.video_source_id}`} className="mb-2 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            <ArrowLeft className="size-3.5" /> {clip.source.title || "Video"}
          </Link>
          <p className="mb-1 font-heading text-sm font-bold text-muted-foreground">
            {clipNumber(clip.sequence)} <span className="ml-2 font-mono text-xs font-normal tabular-nums">{formatHms(clip.start_time)} – {formatHms(clip.end_time)}</span>
          </p>
          <h1 className="font-heading text-2xl font-bold tracking-tight">{clip.title || clip.hook_text || "Untitled clip"}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="rounded-full bg-white/6 px-2 py-0.5 text-foreground/85">{categoryLabel(clip.category)}</span>
            <ClipStatusBadge clip={clip} />
            <StatusBadge status={clip.render_status} />
            {clip.youtube_url && (
              <a href={clip.youtube_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-info hover:underline">
                <ExternalLink className="size-3" /> YouTube
              </a>
            )}
            <Link href={`/app/videos/${clip.video_source_id}`} className="truncate hover:text-foreground hover:underline">
              from {clip.source.title}
            </Link>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {hasMedia && (
            <Link href={`/app/clips/${clip.id}/framing`} className={cn(buttonVariants({ variant: "outline", size: "lg" }), "gradient-border rounded-xl border-transparent")}>
              <Crop /> Smart Framing Editor
            </Link>
          )}
          {canUpload && (
            <Button size="lg" className="glow-red rounded-xl" onClick={() => setUploadOpen(true)}>
              <UploadCloud /> Upload to YouTube
            </Button>
          )}
          {hasMedia && (
            <Button variant="outline" size="lg" className="rounded-xl" onClick={() => setPublishOpen(true)}>
              <Send /> Schedule…
            </Button>
          )}
          <Button variant="ghost" size="lg" className="rounded-xl" onClick={() => setConfirmRemove(true)} disabled={status === "uploading"}>
            <Trash2 /> Remove Clip
          </Button>
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
        <div className="min-w-0 space-y-6">
          {status === "cleaned" && (
            <p className="rounded-xl border border-success/25 bg-success/5 p-3 text-sm text-success">
              Uploaded — local files deleted{clip.freed_bytes ? ` (freed ${formatBytes(clip.freed_bytes)})` : ""}.
            </p>
          )}
          {status === "upload_failed" && clip.upload_error && (
            <p role="alert" className="rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm text-[#ff8098]">Upload failed: {clip.upload_error}</p>
          )}
          <ClipPlayer key={clip.output_url ? "r" : "s"} clip={clip} />
          <TrimEditor key={`${clip.start_time}-${clip.end_time}`} clip={clip} />
          <MetadataEditor key={clip.social_metadata.generated_at ?? "initial"} clip={clip} />
          <SectionCard title="Transcript" icon={Quote}>
            <p className="text-sm leading-relaxed whitespace-pre-wrap text-foreground/85">{clip.transcript_text || "No speech was transcribed in this range."}</p>
          </SectionCard>
        </div>
        <div className="min-w-0 space-y-6">
          {clip.active_jobs.length > 0 && (
            <div className="space-y-2">
              {clip.active_jobs.map((j) => (
                <JobCard key={j.id} job={j} compact />
              ))}
            </div>
          )}
          <RenderPanel clip={clip} />
          <ScorePanel clip={clip} />
          <SectionCard title="Publishing" description="Where this clip is queued or published" icon={Send}>
            <QueueList clipId={clip.id} />
          </SectionCard>
        </div>
      </div>

      <PublishDialog clip={clip} open={publishOpen} onOpenChange={setPublishOpen} />
      {canUpload && <SingleUploadDialog clip={clip} open={uploadOpen} onOpenChange={setUploadOpen} />}
      <RemoveClipDialog clip={clip} open={confirmRemove} onOpenChange={setConfirmRemove} onRemoved={() => router.push(`/app/videos/${clip.video_source_id}`)} />
    </>
  );
}

export function ClipDetailView() {
  const { id } = useParams<{ id: string }>();
  return <Detail key={id} id={id} />;
}
