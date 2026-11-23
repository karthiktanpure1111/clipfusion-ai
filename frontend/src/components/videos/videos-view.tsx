"use client";

import { useDeferredValue, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { Clapperboard, Cloud, Library, Plus, Search, UploadCloud } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { YouTubeIcon } from "@/components/common/brand";
import { CardGridSkeleton, EmptyState, ErrorState, PageHeader, itemVariants, listVariants } from "@/components/common/page";
import { StatusBadge } from "@/components/common/status-badge";
import { DurationPill, VideoThumb } from "@/components/common/video-thumb";
import { JobProgressBar } from "@/components/processing/job-card";
import { useSources } from "@/lib/api/hooks/sources";
import type { SourceOut } from "@/lib/api/types";
import { formatDuration, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

const STATUS_ITEMS = [
  { value: "all", label: "All statuses" },
  { value: "ready", label: "Ready" },
  { value: "processing", label: "Processing" },
  { value: "uploaded", label: "Uploaded" },
  { value: "metadata_only", label: "Metadata only" },
  { value: "importing", label: "Importing" },
  { value: "failed", label: "Failed" },
];

function SourceTypeIcon({ type }: { type: SourceOut["source_type"] }) {
  if (type === "youtube") return <YouTubeIcon className="size-4" />;
  if (type === "cloud") return <Cloud className="size-4 text-info" />;
  return <UploadCloud className="size-4 text-muted-foreground" />;
}

export function SourceCard({ source }: { source: SourceOut }) {
  const job = source.active_job;
  return (
    <motion.article variants={itemVariants} whileHover={{ y: -3 }} className="group relative overflow-hidden rounded-2xl border border-white/7 bg-card/70 transition-colors hover:border-white/15">
      <VideoThumb src={source.thumbnail_url} alt="">
        <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent" />
        <span className="absolute top-2 left-2 rounded-md bg-black/60 p-1 backdrop-blur">
          <SourceTypeIcon type={source.source_type} />
        </span>
        {source.duration != null && <DurationPill className="absolute right-2 bottom-2">{formatDuration(source.duration)}</DurationPill>}
      </VideoThumb>
      <div className="space-y-2.5 p-4">
        <Link href={`/app/videos/${source.id}`} className="line-clamp-2 font-medium outline-none after:absolute after:inset-0 focus-visible:after:rounded-2xl focus-visible:after:ring-2 focus-visible:after:ring-ring">
          {source.title || source.original_filename || "Untitled video"}
        </Link>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <StatusBadge status={source.status} />
          <span className="flex items-center gap-1">
            <Clapperboard className="size-3.5" /> {source.clip_count} clips
          </span>
          <span>· {formatRelative(source.created_at)}</span>
        </div>
        {job && (
          <div className="space-y-1">
            <JobProgressBar value={job.progress} status={job.status} />
            <p className="truncate text-[11px] text-muted-foreground">{job.message ?? "Working…"}</p>
          </div>
        )}
      </div>
    </motion.article>
  );
}

export function VideosView() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("all");
  const query = useDeferredValue(q.trim());
  const { data, isLoading, error, refetch } = useSources({ q: query || undefined, status: status === "all" ? undefined : status });
  const filtered = !!query || status !== "all";

  return (
    <>
      <PageHeader
        title="Videos"
        description="Your library of source videos. Open one to watch it, read the transcript and see the clips cut from it."
        actions={
          <Link href="/app/videos/new" className={cn(buttonVariants({ size: "lg" }), "glow-red rounded-xl")}>
            <Plus /> Add video
          </Link>
        }
      />
      <div className="mb-5 flex flex-col gap-2 sm:flex-row">
        <div className="relative flex-1">
          <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search videos…" aria-label="Search videos" className="h-9 rounded-xl pl-9" />
        </div>
        <Select items={STATUS_ITEMS} value={status} onValueChange={(v) => setStatus(String(v ?? "all"))}>
          <SelectTrigger className="h-9 w-full rounded-xl sm:w-48" aria-label="Filter by status">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_ITEMS.map((s) => (
              <SelectItem key={s.value} value={s.value}>
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {error ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading ? (
        <CardGridSkeleton count={6} className="xl:grid-cols-4" />
      ) : !data?.items.length ? (
        <EmptyState
          icon={Library}
          title={filtered ? "No videos match" : "Your library is empty"}
          description={filtered ? "Try a different search or status filter." : "Upload a long video, paste a YouTube link or import from the cloud to get started."}
          action={
            !filtered && (
              <Link href="/app/videos/new" className={cn(buttonVariants(), "rounded-xl")}>
                <Plus /> Add your first video
              </Link>
            )
          }
        />
      ) : (
        <>
          <motion.div variants={listVariants} initial="hidden" animate="show" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {data.items.map((s) => (
              <SourceCard key={s.id} source={s} />
            ))}
          </motion.div>
          {data.total > data.items.length && (
            <p className="mt-4 text-center text-xs text-muted-foreground">
              Showing {data.items.length} of {data.total}. Refine your search to find older videos.
            </p>
          )}
        </>
      )}
    </>
  );
}
