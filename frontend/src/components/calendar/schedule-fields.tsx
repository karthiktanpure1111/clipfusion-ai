"use client";

import { useState } from "react";
import { Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Segmented } from "@/components/common/segmented";
import type { ScheduleInput } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export type ScheduleDraft = Required<Pick<ScheduleInput, "mode" | "frequency_type" | "default_privacy">> & {
  connected_account_id: string;
  name: string;
  interval_minutes: number;
  times_of_day: string[];
  days_of_week: number[];
  timezone: string;
  daily_limit: number;
  window_start: string;
  window_end: string;
  auto_min_score: number;
  rights_confirmed: boolean;
};

type SetDraft = (patch: Partial<ScheduleDraft>) => void;

// Backend convention: 0 = Monday … 6 = Sunday.
export const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const INTERVALS = [60, 120, 180, 360];

const chip = (on: boolean) =>
  cn(
    "h-8 rounded-full px-3 text-xs font-medium ring-1 transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
    on ? "bg-primary text-white ring-primary" : "bg-white/4 text-muted-foreground ring-white/10 hover:text-foreground",
  );

export function ModeField({ draft, set }: { draft: ScheduleDraft; set: SetDraft }) {
  return (
    <div className="space-y-2">
      <Label>Mode</Label>
      <Segmented
        ariaLabel="Schedule mode"
        size="sm"
        value={draft.mode}
        onChange={(mode) => set({ mode })}
        options={[
          { value: "review", label: "Review" },
          { value: "automatic", label: "Automatic" },
        ]}
      />
      <p className="text-xs text-muted-foreground">
        {draft.mode === "review"
          ? "Only clips you schedule yourself are posted; each takes the next free slot."
          : "Clips that meet the minimum AI highlight score (estimate) enter the queue and publish automatically. A clip with unreviewed framing warnings isn't published: its post fails with an explanation until you review the framing."}
      </p>
    </div>
  );
}

export function FrequencyField({ draft, set }: { draft: ScheduleDraft; set: SetDraft }) {
  const [time, setTime] = useState("18:00");
  const custom = !INTERVALS.includes(draft.interval_minutes);
  return (
    <div className="space-y-2">
      <Label>Frequency</Label>
      <Segmented
        ariaLabel="Frequency type"
        size="sm"
        value={draft.frequency_type}
        onChange={(frequency_type) => set({ frequency_type })}
        options={[
          { value: "interval", label: "Every few hours" },
          { value: "times", label: "Specific times" },
        ]}
      />
      {draft.frequency_type === "interval" ? (
        <div className="flex flex-wrap items-center gap-2">
          {INTERVALS.map((m) => (
            <button key={m} type="button" aria-pressed={draft.interval_minutes === m} onClick={() => set({ interval_minutes: m })} className={chip(draft.interval_minutes === m)}>
              {m / 60} h
            </button>
          ))}
          <span className={cn("flex items-center gap-1.5 rounded-full px-2 text-xs ring-1", custom ? "ring-primary" : "ring-white/10")}>
            <Input
              aria-label="Custom interval in minutes (15 to 10080)"
              type="number"
              min={15}
              max={10080}
              value={draft.interval_minutes}
              onChange={(e) => set({ interval_minutes: Number(e.target.value) || 0 })}
              className="h-8 w-20 border-0 bg-transparent text-right dark:bg-transparent"
            />
            min
          </span>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {draft.times_of_day.map((t) => (
            <span key={t} className="flex h-8 items-center gap-1 rounded-full bg-white/6 pr-1.5 pl-3 text-xs tabular-nums ring-1 ring-white/10">
              {t}
              <button type="button" aria-label={`Remove ${t}`} className="rounded-full p-0.5 hover:bg-white/10" onClick={() => set({ times_of_day: draft.times_of_day.filter((x) => x !== t) })}>
                <X className="size-3" />
              </button>
            </span>
          ))}
          <Input type="time" aria-label="Time of day" value={time} onChange={(e) => setTime(e.target.value)} className="h-8 w-28 rounded-full" />
          <Button
            type="button"
            variant="outline"
            size="icon-sm"
            className="rounded-full"
            aria-label="Add time"
            disabled={!time || draft.times_of_day.includes(time)}
            onClick={() => set({ times_of_day: [...draft.times_of_day, time].sort() })}
          >
            <Plus />
          </Button>
        </div>
      )}
    </div>
  );
}

export function DaysField({ draft, set }: { draft: ScheduleDraft; set: SetDraft }) {
  return (
    <div className="space-y-2">
      <Label>Days</Label>
      <div className="flex flex-wrap gap-1.5">
        {DAY_LABELS.map((label, i) => {
          const on = draft.days_of_week.includes(i);
          return (
            <button
              key={label}
              type="button"
              aria-pressed={on}
              onClick={() => set({ days_of_week: on ? draft.days_of_week.filter((d) => d !== i) : [...draft.days_of_week, i].sort((a, b) => a - b) })}
              className={chip(on)}
            >
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
