"use client";

import { useState } from "react";
import { AlertOctagon, Ban, CalendarClock, CheckCircle2, ExternalLink, ListVideo, Loader2, RotateCcw, SkipForward, UploadCloud } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { RightsCheckbox } from "@/components/common/fields";
import { SectionCard } from "@/components/common/page";
import { Segmented } from "@/components/common/segmented";
import { StatusBadge } from "@/components/common/status-badge";
import { JobProgressBar } from "@/components/processing/job-card";
import { useAutoSchedule } from "@/lib/api/hooks/timeline";
import { useCreateUploadRun, useUploadRunAction, useUploadRuns } from "@/lib/api/hooks/uploads";
import type { ClipOut, UploadRunItem, UploadRunOut } from "@/lib/api/types";
import { clipNumber, formatRelative, titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";
import { AutoScheduleToggle, gapLabel, ScheduledList, SchedulePlanner } from "./schedule-panel";
import { canUploadWith, startRun, UploadTargetFields, useUploadTarget } from "./upload-controls";

const RUN_LABEL: Record<UploadRunOut["status"], string> = {
  running: "Uploading",
  completed: "Completed",
  failed: "Stopped — upload failed",
  cancelled: "Cancelled",
};
const MODE_LABEL: Record<UploadRunOut["mode"], string> = {
  single: "Single clip",
  next: "Next clip",
  remaining: "All remaining clips",
};

/** Clips a run (or a schedule) would take: rendered, not uploaded and not scheduled yet (a failed one is retried first). */
export const uploadableClips = (clips: ClipOut[]) =>
  clips.filter((c) => (c.status === "generated" || c.status === "upload_failed") && !c.scheduled_at).sort((a, b) => (a.sequence ?? 1e9) - (b.sequence ?? 1e9));

function ItemRow({ item, current }: { item: UploadRunItem; current: boolean }) {
  const measurable = typeof item.progress === "number" && Number.isFinite(item.progress);
  return (
    <li className={cn("rounded-lg border px-2.5 py-2 text-sm", current ? "border-ig-purple/35 bg-ig-purple/[0.06]" : "border-white/6 bg-black/10")}>
      <div className="flex items-center gap-2">
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{clipNumber(item.sequence)}</span>
        <span className="min-w-0 flex-1 truncate">{item.title || "Untitled clip"}</span>
        <StatusBadge status={item.status === "failed" ? "upload_failed" : item.status} label={item.status === "pending" ? "Waiting" : undefined} />
      </div>
      {item.status === "uploading" && measurable && (
        <div className="mt-1.5 flex items-center gap-2">
          <div className="flex-1">
            <JobProgressBar value={item.progress as number} status="running" />
          </div>
          <span className="w-9 text-right text-[11px] text-muted-foreground tabular-nums">{Math.round((item.progress as number) * 100)}%</span>
        </div>
      )}
      {item.status === "uploading" && !measurable && <p className="mt-1 text-[11px] text-muted-foreground">Sending to YouTube…</p>}
      {item.youtube_url && (
        <a href={item.youtube_url} target="_blank" rel="noreferrer" className="mt-1 inline-flex items-center gap-1 text-xs text-info hover:underline">
          <ExternalLink className="size-3" /> {item.youtube_url.replace(/^https?:\/\/(www\.)?/, "")}
        </a>
      )}
      {item.error && <p className="mt-1 text-xs break-words text-[#ff8098]">{item.error}</p>}
    </li>
  );
}

function RunStatus({ run }: { run: UploadRunOut }) {
  const action = useUploadRunAction();
  const running = run.status === "running";
  const currentIdx = run.items.findIndex((i) => i.clip_id === run.current_clip_id);
  const n = currentIdx >= 0 ? currentIdx + 1 : Math.min(run.total, run.uploaded + 1);
  return (
    <div className="space-y-3 rounded-xl border border-white/8 bg-black/15 p-3" aria-live="polite">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={run.status === "completed" ? "succeeded" : run.status} label={RUN_LABEL[run.status] ?? titleCase(run.status)} />
        <span className="text-xs text-muted-foreground">
          {MODE_LABEL[run.mode] ?? titleCase(run.mode)} · {titleCase(run.privacy)} · {formatRelative(run.updated_at || run.created_at)}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="rounded-lg bg-white/4 p-2">
          <p className="font-heading text-lg font-bold tabular-nums">{running ? `${n}/${run.total}` : run.total}</p>
          <p className="text-[10px] text-muted-foreground uppercase">{running ? "Current upload" : "In this run"}</p>
        </div>
        <div className="rounded-lg bg-white/4 p-2">
          <p className="font-heading text-lg font-bold text-success tabular-nums">{run.uploaded}</p>
          <p className="text-[10px] text-muted-foreground uppercase">Uploaded</p>
        </div>
        <div className="rounded-lg bg-white/4 p-2">
          <p className="font-heading text-lg font-bold tabular-nums">{run.remaining}</p>
          <p className="text-[10px] text-muted-foreground uppercase">Remaining</p>
        </div>
      </div>
      {run.status === "failed" && (
        <div role="alert" className="flex gap-2 rounded-lg bg-destructive/8 p-2.5 text-xs text-[#ff8098]">
          <AlertOctagon className="mt-px size-3.5 shrink-0" />
          <span className="break-words">
            {run.last_error || "The upload failed."} Nothing was skipped — Retry uploads the failed clip again and then continues in order.
          </span>
        </div>
      )}
      {run.items.length > 0 && (
        <ol className="max-h-72 space-y-1.5 overflow-y-auto pr-1">
          {run.items.map((i) => (
            <ItemRow key={i.clip_id} item={i} current={running && i.clip_id === run.current_clip_id} />
          ))}
        </ol>
      )}
      <div className="flex flex-wrap gap-2">
        {run.status === "failed" && (
          <Button
            size="sm"
            onClick={() =>
              action.mutate(
                { id: run.id, action: "retry" },
                {
                  onSuccess: () => toast.success("Retrying the failed upload"),
                },
              )
            }
            disabled={action.isPending}
          >
            <RotateCcw /> Retry
          </Button>
        )}
        {running && run.cancel_requested && <p className="text-xs text-warning">Cancelling — the run stops when the current upload finishes.</p>}
        {running && !run.cancel_requested && (
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              action.mutate(
                { id: run.id, action: "cancel" },
                {
                  onSuccess: () => toast.info("Cancelling — the run stops after the current upload finishes"),
                },
              )
            }
            disabled={action.isPending}
          >
            <Ban /> Cancel
          </Button>
        )}
      </div>
      {running && <p className="text-[11px] text-muted-foreground">Cancel stops after the current upload; a transfer is never interrupted midway.</p>}
    </div>
  );
}

export function UploadManager({ sourceId, clips }: { sourceId: string; clips: ClipOut[] }) {
  const runs = useUploadRuns(sourceId);
  const target = useUploadTarget();
  const create = useCreateUploadRun();
  const [rights, setRights] = useState(false);
  const [mode, setMode] = useState<"now" | "schedule">("schedule");
  const auto = useAutoSchedule();
  const [gapPick, setGap] = useState<number | null>(null);
  const gap = gapPick ?? auto.data?.gap_minutes ?? 60;
  const latest = runs.data?.[0];
  const running = runs.data?.find((r) => r.status === "running");
  const pending = uploadableClips(clips);
  const next = pending[0];
  const ready = canUploadWith(target.account) && rights && !running && !create.isPending;
  const uploadedLinks = clips.filter((c) => c.youtube_url).sort((a, b) => (a.sequence ?? 0) - (b.sequence ?? 0));

  const start = async (mode: "next" | "remaining") => {
    if (!target.accountId) return;
    const ok = await startRun(create, {
      video_source_id: sourceId,
      connected_account_id: target.accountId,
      mode,
      privacy: target.privacy,
      rights_confirmed: true,
    });
    if (ok)
      toast.success(
        mode === "next" ? `Uploading ${clipNumber(next?.sequence)}` : `Uploading ${pending.length} clip${pending.length === 1 ? "" : "s"} in order`,
      );
  };

  return (
    <SectionCard title="Upload to YouTube" description="One clip at a time, in timeline order — right now, or scheduled a set time apart." icon={UploadCloud}>
      <div className="space-y-4">
        <UploadTargetFields target={target} idPrefix={`run-${sourceId}`} />
        <RightsCheckbox id={`rights-run-${sourceId}`} checked={rights} onChange={setRights} />
        <Segmented
          ariaLabel="Upload now or schedule"
          value={mode}
          onChange={setMode}
          className="w-full sm:w-auto"
          options={[
            {
              value: "schedule" as const,
              label: (
                <>
                  <CalendarClock className="size-4" /> Schedule · {gapLabel(gap)} apart
                </>
              ),
            },
            {
              value: "now" as const,
              label: (
                <>
                  <UploadCloud className="size-4" /> Upload now
                </>
              ),
            },
          ]}
        />
        {mode === "schedule" ? (
          <SchedulePlanner sourceId={sourceId} ready={pending} target={target} rights={rights} gap={gap} onGapChange={setGap} />
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button className="rounded-xl" disabled={!ready || !next} onClick={() => start("next")}>
              {create.isPending && create.variables?.mode === "next" ? <Loader2 className="animate-spin" /> : <SkipForward />} Upload Next
              {next ? ` (${clipNumber(next.sequence)})` : ""}
            </Button>
            <Button variant="outline" className="rounded-xl" disabled={!ready || !pending.length} onClick={() => start("remaining")}>
              {create.isPending && create.variables?.mode === "remaining" ? <Loader2 className="animate-spin" /> : <ListVideo />} Upload Remaining (
              {pending.length})
            </Button>
          </div>
        )}
        <ScheduledList sourceId={sourceId} clips={clips} />
        <AutoScheduleToggle target={target} gap={gap} />
        {running ? (
          <p className="text-xs text-muted-foreground">An upload run is in progress — you can start another one when it has finished or been cancelled.</p>
        ) : !pending.length && clips.length > 0 ? (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <CheckCircle2 className="size-3.5 text-success" /> No generated clips are waiting to be uploaded or scheduled.
          </p>
        ) : null}
        {runs.error ? <p className="text-xs text-muted-foreground">Upload status is unavailable right now.</p> : latest ? <RunStatus run={latest} /> : null}
        {uploadedLinks.length > 0 && (
          <div className="space-y-1">
            <p className="text-xs font-medium">On YouTube</p>
            <ul className="space-y-0.5">
              {uploadedLinks.map((c) => (
                <li key={c.id} className="flex items-center gap-2 text-xs">
                  <span className="shrink-0 font-mono text-muted-foreground">{clipNumber(c.sequence)}</span>
                  <a href={c.youtube_url as string} target="_blank" rel="noreferrer" className="truncate text-info hover:underline">
                    {c.youtube_url}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </SectionCard>
  );
}
