"use client";

import { AlertOctagon, Check, Circle, CircleSlash, Cpu, Gauge, Loader2, Timer, Zap } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { SectionCard } from "@/components/common/page";
import { StatusBadge } from "@/components/common/status-badge";
import { useNow } from "@/components/common/use-now";
import { JobProgressBar } from "@/components/processing/job-card";
import type { PipelineOut, PipelineStage } from "@/lib/api/types";
import { formatDuration, stageLabel, titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<PipelineOut["status"], string> = { idle: "Not started", running: "Processing", completed: "Completed", failed: "Failed" };
const STAGE_STATUS_LABEL: Record<PipelineStage["status"], string> = { pending: "Waiting", running: "Running", done: "Done", failed: "Failed", skipped: "Skipped" };

/** Seconds spent in a stage: the server's value, else measured live from started_at while it runs. */
function stageSeconds(s: PipelineStage, now: number): number | null {
  if (s.seconds != null) return s.seconds;
  if (s.status === "running" && s.started_at && now) return Math.max(0, (now - new Date(s.started_at).getTime()) / 1000);
  return null;
}

function StageIcon({ status }: { status: PipelineStage["status"] }) {
  const cls = "size-4 shrink-0";
  if (status === "done") return <Check className={cn(cls, "text-success")} aria-hidden />;
  if (status === "running") return <Loader2 className={cn(cls, "animate-spin text-[#c79cf0]")} aria-hidden />;
  if (status === "failed") return <AlertOctagon className={cn(cls, "text-[#ff5c7a]")} aria-hidden />;
  if (status === "skipped") return <CircleSlash className={cn(cls, "text-muted-foreground/60")} aria-hidden />;
  return <Circle className={cn(cls, "text-muted-foreground/40")} aria-hidden />;
}

function StageRow({ stage, now }: { stage: PipelineStage; now: number }) {
  const secs = stageSeconds(stage, now);
  const measurable = typeof stage.progress === "number" && Number.isFinite(stage.progress);
  const label = stage.key === "completed" ? stage.label || stageLabel(stage.key) : stageLabel(stage.key) || stage.label;
  return (
    <li
      className={cn("rounded-xl border px-3 py-2.5", stage.status === "running" ? "border-ig-purple/35 bg-ig-purple/[0.06]" : "border-white/6 bg-black/10")}
      aria-current={stage.status === "running" ? "step" : undefined}
    >
      <div className="flex items-center gap-2.5">
        <StageIcon status={stage.status} />
        <span className={cn("min-w-0 flex-1 truncate text-sm", stage.status === "pending" ? "text-muted-foreground" : "font-medium")}>{label}</span>
        <span className="text-[11px] text-muted-foreground">{STAGE_STATUS_LABEL[stage.status] ?? titleCase(stage.status)}</span>
        <span className="w-14 text-right text-xs text-muted-foreground tabular-nums" title="Time spent in this stage">
          {secs != null ? formatDuration(secs) : ""}
        </span>
      </div>
      {stage.status === "running" && measurable && (
        <div className="mt-2 flex items-center gap-2 pl-6.5">
          <div className="flex-1">
            <JobProgressBar value={stage.progress as number} status="running" />
          </div>
          <span className="w-9 text-right text-[11px] text-muted-foreground tabular-nums">{Math.round((stage.progress as number) * 100)}%</span>
        </div>
      )}
      {stage.message && stage.status !== "failed" && <p className="mt-1 pl-6.5 text-xs text-muted-foreground">{stage.message}</p>}
      {stage.error && (
        <p className="mt-1.5 flex gap-1.5 rounded-lg bg-destructive/8 p-2 pl-2 text-xs break-words text-[#ff8098]">
          <AlertOctagon className="mt-px size-3.5 shrink-0" /> {stage.error}
        </p>
      )}
    </li>
  );
}

function Fact({ icon: Icon, label, children }: { icon: typeof Cpu; label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 items-center gap-2 rounded-xl border border-white/7 bg-black/15 px-3 py-2">
      <Icon className="size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0">
        <p className="text-[10px] tracking-wide text-muted-foreground uppercase">{label}</p>
        <p className="truncate text-sm">{children}</p>
      </div>
    </div>
  );
}

export function PipelineDashboard({ pipeline, loading }: { pipeline: PipelineOut | undefined; loading?: boolean }) {
  const now = useNow();
  if (loading) return <Skeleton className="h-64 rounded-2xl" />;
  if (!pipeline || (pipeline.status === "idle" && !pipeline.started_at)) return null;
  const current = pipeline.stages.find((s) => s.status === "running");
  const failed = pipeline.stages.find((s) => s.status === "failed");
  const running = pipeline.status === "running";
  const elapsed =
    running && pipeline.started_at && now ? Math.max(pipeline.elapsed_seconds ?? 0, (now - new Date(pipeline.started_at).getTime()) / 1000) : pipeline.elapsed_seconds;
  const dev = pipeline.device?.transcription;
  const c = pipeline.clips;

  return (
    <SectionCard
      title="Processing"
      description={running ? (current ? `Now: ${stageLabel(current.key) || current.label}` : "Working…") : pipeline.status === "failed" ? `Stopped at ${failed ? stageLabel(failed.key) || failed.label : "a stage"}` : undefined}
      icon={Gauge}
      actions={<StatusBadge status={pipeline.status === "completed" ? "succeeded" : pipeline.status} label={STATUS_LABEL[pipeline.status]} />}
    >
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        <Fact icon={Zap} label="Mode">{pipeline.mode ? titleCase(pipeline.mode) : "–"}</Fact>
        <Fact icon={Cpu} label="Transcription device">
          {dev ? (dev === "cuda" ? "GPU (CUDA)" : "CPU") : "–"}
          {pipeline.device?.model ? ` · ${pipeline.device.model}` : ""}
        </Fact>
        <Fact icon={Timer} label="Elapsed">{elapsed != null ? formatDuration(elapsed) : "–"}</Fact>
        <Fact icon={Timer} label="Estimated time left">
          {running && pipeline.eta_seconds != null ? `≈ ${formatDuration(pipeline.eta_seconds)}` : running ? "Not measurable yet" : "–"}
        </Fact>
      </div>
      <ol className="mt-4 space-y-1.5" aria-label="Processing stages">
        {pipeline.stages.map((s) => (
          <StageRow key={s.key} stage={s} now={now} />
        ))}
      </ol>
      {c && c.total > 0 && (
        <p className="mt-3 text-xs text-muted-foreground">
          {c.total} clip{c.total === 1 ? "" : "s"}: {c.pending ? `${c.pending} not rendered yet · ` : ""}{c.processing} processing · {c.generated} generated · {c.uploaded} uploaded
          {c.upload_failed ? ` · ${c.upload_failed} upload failed` : ""} · {c.cleaned} cleaned · {c.removed} removed
        </p>
      )}
    </SectionCard>
  );
}
