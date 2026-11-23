"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { AlertOctagon, Ban, Check, Clock, Cpu, Download, FileText, FileVideo, Film, Loader2, RotateCcw, ScanFace, Send, Sparkles, Tags, TrendingUp } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/common/status-badge";
import { useCancelJob, useRetryJob } from "@/lib/api/hooks/jobs";
import { isJobActive } from "@/lib/api/hooks/common";
import type { JobOut } from "@/lib/api/types";
import { formatRelative, jobTypeLabel, stageLabel } from "@/lib/format";
import { cn } from "@/lib/utils";

/** v2 pipeline stages (docs/API_V2.md) and the older analysis-job stages, with friendly names. */
const PIPELINE_KEYS = ["ingestion", "audio", "transcription", "video_analysis", "highlights", "clip_generation", "metadata"];
const LEGACY_KEYS = ["probe", "audio", "transcription", "scenes", "visual", "highlights"];
const toStages = (keys: string[]) => keys.map((key) => ({ key, label: stageLabel(key) }));

const TYPE_ICON: Record<string, LucideIcon> = {
  analysis: Sparkles,
  clip_vision: ScanFace,
  render: Film,
  publish: Send,
  import: Download,
  metadata: Tags,
  transcribe_clip: FileText,
  analytics: TrendingUp,
};

export function StageStepper({ job }: { job: JobOut }) {
  const stages = toStages(job.stage && LEGACY_KEYS.includes(job.stage) && !PIPELINE_KEYS.includes(job.stage) ? LEGACY_KEYS : PIPELINE_KEYS);
  const idx = stages.findIndex((s) => s.key === job.stage);
  const done = job.status === "succeeded";
  return (
    <ol className={cn("grid gap-1.5", stages.length > 6 ? "grid-cols-4 sm:grid-cols-7" : "grid-cols-6")} aria-label="Processing stages">
      {stages.map((s, i) => {
        const state = done || (idx >= 0 && i < idx) ? "done" : i === idx ? (job.status === "failed" ? "failed" : "current") : "todo";
        return (
          <li key={s.key} className="min-w-0" aria-current={state === "current" ? "step" : undefined}>
            <div
              className={cn(
                "h-1.5 rounded-full transition-colors",
                state === "done" && "bg-ig-gradient",
                state === "current" && "animate-pulse bg-ig-pink/70",
                state === "failed" && "bg-destructive",
                state === "todo" && "bg-white/8",
              )}
            />
            <div className={cn("mt-1.5 flex items-center gap-1 truncate text-[10px] sm:text-[11px]", state === "todo" ? "text-muted-foreground/60" : "text-foreground/90")}>
              {state === "done" && <Check className="size-3 shrink-0 text-success" />}
              {state === "current" && <Loader2 className="size-3 shrink-0 animate-spin" />}
              <span className="truncate">{s.label}</span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function JobProgressBar({ value, status }: { value: number; status: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="h-1.5 overflow-hidden rounded-full bg-white/8" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
      <motion.div
        className={cn("h-full rounded-full", status === "failed" ? "bg-destructive" : status === "succeeded" ? "bg-success" : "bg-brand-gradient")}
        initial={false}
        animate={{ width: `${pct}%` }}
        transition={{ duration: 0.5, ease: "easeOut" }}
      />
    </div>
  );
}

export function JobCard({ job, compact = false }: { job: JobOut; compact?: boolean }) {
  const cancel = useCancelJob();
  const retry = useRetryJob();
  const Icon = TYPE_ICON[job.job_type] ?? Cpu;
  const active = isJobActive(job.status);
  const pct = Math.round(job.progress * 100);
  const subject = job.clip_title || job.source_title;
  const href = job.clip_id ? `/app/clips/${job.clip_id}` : job.video_source_id ? `/app/videos/${job.video_source_id}` : null;

  return (
    <div className={cn("rounded-2xl border border-white/7 bg-card/70 p-4 transition-colors", active && "border-white/10 bg-card")}>
      <div className="flex items-start gap-3">
        <span className={cn("grid size-9 shrink-0 place-items-center rounded-xl ring-1", active ? "bg-ig-purple/15 ring-ig-purple/30" : "bg-white/4 ring-white/10")}>
          <Icon className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{jobTypeLabel(job.job_type)}</span>
            <StatusBadge status={job.status} />
            {job.attempts > 1 && (
              <span className="text-[11px] text-muted-foreground">
                attempt {job.attempts}/{job.max_attempts}
              </span>
            )}
          </div>
          {subject && (
            <div className="mt-0.5 flex items-center gap-1.5 truncate text-sm text-muted-foreground">
              <FileVideo className="size-3.5 shrink-0" />
              {href ? (
                <Link href={href} className="truncate hover:text-foreground hover:underline">
                  {subject}
                </Link>
              ) : (
                <span className="truncate">{subject}</span>
              )}
            </div>
          )}
        </div>
        {!compact && (
          <div className="flex shrink-0 items-center gap-1.5">
            {active && (
              <Button variant="outline" size="sm" onClick={() => cancel.mutate(job.id)} disabled={cancel.isPending}>
                <Ban /> Cancel
              </Button>
            )}
            {(job.status === "failed" || job.status === "cancelled") && (
              <Button variant="outline" size="sm" onClick={() => retry.mutate(job.id)} disabled={retry.isPending}>
                <RotateCcw /> Retry
              </Button>
            )}
          </div>
        )}
      </div>

      {(active || !compact) && (
        <div className="mt-4 space-y-3">
          {job.job_type === "analysis" && <StageStepper job={job} />}
          <div className="flex items-center gap-3">
            <div className="flex-1">
              <JobProgressBar value={job.status === "succeeded" ? 1 : job.progress} status={job.status} />
            </div>
            <span className="w-10 text-right text-xs text-muted-foreground tabular-nums">{job.status === "succeeded" ? 100 : pct}%</span>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
            <span className="min-w-0 truncate">
              {job.stage && job.stage !== "done" && <span className="text-foreground/80">{stageLabel(job.stage)} · </span>}
              {job.message ?? (active ? "Working…" : "")}
            </span>
            <span className="flex items-center gap-1">
              <Clock className="size-3" />
              {job.finished_at ? `finished ${formatRelative(job.finished_at)}` : job.started_at ? `started ${formatRelative(job.started_at)}` : `queued ${formatRelative(job.created_at)}`}
            </span>
          </div>
          {job.status === "retrying" && job.next_attempt_at && (
            <p className="text-xs text-warning">Retrying {formatRelative(job.next_attempt_at)}</p>
          )}
        </div>
      )}

      {job.error && (job.status === "failed" || job.status === "retrying") && (
        <div className="mt-3 flex gap-2 rounded-xl bg-destructive/8 p-2.5 text-xs text-[#ff8098]">
          <AlertOctagon className="mt-px size-3.5 shrink-0" />
          <span className="break-words">{job.error}</span>
        </div>
      )}
    </div>
  );
}
