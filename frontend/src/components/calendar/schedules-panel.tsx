"use client";

import { useState } from "react";
import { CalendarPlus, ChevronDown, Pause, Pencil, Play, Plus, Repeat, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { PLATFORM_META, PlatformIcon } from "@/components/common/brand";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { EmptyState, ErrorState, SectionCard } from "@/components/common/page";
import { StatusBadge } from "@/components/common/status-badge";
import { useAccounts, useScheduleAction, useSchedulePreview, useSchedules } from "@/lib/api/hooks/publishing";
import type { ScheduleOut } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ScheduleDialog } from "./schedule-dialog";
import { DAY_LABELS } from "./schedule-fields";

function frequencyText(s: ScheduleOut): string {
  const days = s.days_of_week.length === 7 ? "every day" : s.days_of_week.map((d) => DAY_LABELS[d]).join(", ");
  const window = s.window_start && s.window_end ? `, ${s.window_start}–${s.window_end}` : s.window_start ? `, from ${s.window_start}` : s.window_end ? `, until ${s.window_end}` : "";
  if (s.frequency_type === "times") return `${s.times_of_day.join(", ")} · ${days}${window}`;
  const m = s.interval_minutes ?? 0;
  return `Every ${m % 60 === 0 ? `${m / 60} h` : `${m} min`} · ${days}${window}`;
}

function PreviewSlots({ schedule }: { schedule: ScheduleOut }) {
  const { data, isLoading, error } = useSchedulePreview(schedule.id, 6);
  if (isLoading) return <Skeleton className="h-8 rounded-lg" />;
  if (error) return <p className="text-xs text-[#ff8098]">{error.message}</p>;
  if (!data?.slots.length) return <p className="text-xs text-muted-foreground">No upcoming slots match these rules.</p>;
  return (
    <div>
      <p className="mb-1.5 text-[11px] text-muted-foreground">Upcoming slots ({data.timezone})</p>
      <ul className="flex flex-wrap gap-1.5">
        {data.slots.map((s) => (
          <li key={s.at} className="rounded-lg bg-white/5 px-2 py-1 text-xs tabular-nums ring-1 ring-white/8">
            {formatDateTime(s.at, data.timezone)}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ScheduleRow({ schedule, accountName, onEdit, onDelete }: { schedule: ScheduleOut; accountName: string; onEdit: () => void; onDelete: () => void }) {
  const action = useScheduleAction();
  const [preview, setPreview] = useState(false);
  return (
    <li className="space-y-3 rounded-2xl border border-white/7 bg-black/15 p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1">
          <p className="flex flex-wrap items-center gap-2 font-medium">
            <PlatformIcon platform={schedule.platform} /> {schedule.name}
            <StatusBadge status={schedule.is_active ? "active" : "paused"} />
            <StatusBadge status={schedule.mode} label={schedule.mode === "automatic" ? "Automatic" : "Review"} tone={schedule.mode === "automatic" ? "brand" : "neutral"} />
          </p>
          <p className="text-sm text-muted-foreground">{accountName || PLATFORM_META[schedule.platform]?.label}</p>
          <p className="text-xs text-muted-foreground">
            {frequencyText(schedule)} · {schedule.timezone} · max {schedule.daily_limit}/day
            {schedule.mode === "automatic" && ` · AI score (estimate) ≥ ${schedule.auto_min_score}`}
            {schedule.platform === "youtube" && ` · ${schedule.default_privacy}`}
          </p>
        </div>
        <div className="flex shrink-0 gap-1">
          <Button
            variant="outline"
            size="sm"
            disabled={action.isPending}
            onClick={() => action.mutate({ id: schedule.id, action: schedule.is_active ? "pause" : "resume" }, { onSuccess: () => toast.success(schedule.is_active ? "Schedule paused" : "Schedule resumed") })}
          >
            {schedule.is_active ? <Pause /> : <Play />} {schedule.is_active ? "Pause" : "Resume"}
          </Button>
          <Button variant="ghost" size="icon-sm" aria-label={`Edit ${schedule.name}`} onClick={onEdit}>
            <Pencil />
          </Button>
          <Button variant="ghost" size="icon-sm" aria-label={`Delete ${schedule.name}`} onClick={onDelete}>
            <Trash2 />
          </Button>
        </div>
      </div>
      <button type="button" aria-expanded={preview} onClick={() => setPreview((p) => !p)} className="flex items-center gap-1 rounded text-xs text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
        <ChevronDown className={cn("size-3.5 transition-transform", preview && "rotate-180")} /> Preview upcoming slots
      </button>
      {preview && <PreviewSlots schedule={schedule} />}
    </li>
  );
}

export function SchedulesPanel({ tz }: { tz: string }) {
  const { data, isLoading, error, refetch } = useSchedules();
  const accounts = useAccounts();
  const action = useScheduleAction();
  const [dialog, setDialog] = useState<{ open: boolean; schedule: ScheduleOut | null }>({ open: false, schedule: null });
  const [toDelete, setToDelete] = useState<ScheduleOut | null>(null);
  const create = () => setDialog({ open: true, schedule: null });

  return (
    <>
      <SectionCard
        className="mt-6"
        title="Schedules"
        description="Recurring posting rules per account."
        icon={Repeat}
        actions={
          <Button size="sm" onClick={create}>
            <Plus /> New schedule
          </Button>
        }
      >
        {error ? (
          <ErrorState error={error} onRetry={() => refetch()} />
        ) : isLoading ? (
          <Skeleton className="h-28 rounded-xl" />
        ) : data?.length ? (
          <ul className="space-y-3">
            {data.map((s) => (
              <ScheduleRow
                key={s.id}
                schedule={s}
                accountName={accounts.data?.find((a) => a.id === s.connected_account_id)?.display_name ?? ""}
                onEdit={() => setDialog({ open: true, schedule: s })}
                onDelete={() => setToDelete(s)}
              />
            ))}
          </ul>
        ) : (
          <EmptyState icon={CalendarPlus} title="No schedules yet" description="Create one to post clips every few hours or at fixed times." action={<Button onClick={create}>Create a schedule</Button>} className="py-8" />
        )}
      </SectionCard>
      <ScheduleDialog open={dialog.open} schedule={dialog.schedule} tz={tz} onOpenChange={(open) => setDialog((d) => ({ ...d, open }))} />
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={`Delete “${toDelete?.name ?? ""}”?`}
        description="No new posts are queued by this schedule. Check the calendar for posts it already queued."
        confirmLabel="Delete schedule"
        destructive
        pending={action.isPending}
        onConfirm={() => toDelete && action.mutate({ id: toDelete.id, action: "delete" }, { onSuccess: () => setToDelete(null) })}
      />
    </>
  );
}
