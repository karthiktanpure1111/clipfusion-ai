"use client";

import { format } from "date-fns";
import { PlatformIcon } from "@/components/common/brand";
import type { CalendarEntry } from "@/lib/api/types";
import { formatTime, titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const PLATFORM_CHIP: Record<string, string> = {
  youtube: "border-l-[#FF0033] bg-[#FF0033]/10 hover:bg-[#FF0033]/20",
  instagram: "border-l-[#DD2A7B] bg-[#DD2A7B]/10 hover:bg-[#DD2A7B]/20",
};

export function statusDot(status: string): string {
  if (status === "published" || status === "success") return "bg-success";
  if (status === "failed") return "bg-destructive";
  if (status === "pending_approval") return "bg-warning";
  return "bg-info";
}

export function EntryChip({ entry, tz, onOpen }: { entry: CalendarEntry; tz: string; onOpen: (e: CalendarEntry) => void }) {
  return (
    <button
      type="button"
      onClick={() => onOpen(entry)}
      title={`${entry.clip_title || "Clip"} · ${titleCase(entry.status)}`}
      className={cn(
        "flex w-full items-center gap-1.5 rounded-md border-l-2 px-1.5 py-1 text-left text-[11px] transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
        PLATFORM_CHIP[entry.platform] ?? "border-l-white/30 bg-white/5",
        entry.status === "failed" && "ring-1 ring-destructive/40",
      )}
    >
      <span className={cn("size-1.5 shrink-0 rounded-full", statusDot(entry.status))} aria-hidden />
      <span className="shrink-0 tabular-nums">{formatTime(entry.at, tz)}</span>
      <PlatformIcon platform={entry.platform} className="size-3 shrink-0" />
      <span className="truncate">{entry.clip_title || "Untitled clip"}</span>
      <span className="sr-only">{titleCase(entry.status)}</span>
    </button>
  );
}

/** `days` are yyyy-MM-dd keys (42 cells, Monday first); entries are grouped by local day key. */
export function MonthGrid({
  days,
  month,
  today,
  byDay,
  tz,
  onOpen,
  onShowDay,
}: {
  days: Date[];
  month: number;
  today: string;
  byDay: Map<string, CalendarEntry[]>;
  tz: string;
  onOpen: (e: CalendarEntry) => void;
  onShowDay: () => void;
}) {
  return (
    <div className="overflow-hidden rounded-2xl border border-white/7 bg-card/60">
      <div className="grid grid-cols-7 border-b border-white/6 text-center text-[11px] font-medium text-muted-foreground">
        {WEEKDAYS.map((d) => (
          <div key={d} className="py-2">
            {d}
          </div>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {days.map((d) => {
          const key = format(d, "yyyy-MM-dd");
          const entries = byDay.get(key) ?? [];
          const outside = d.getMonth() !== month;
          return (
            <div key={key} className={cn("min-h-24 space-y-1 border-r border-b border-white/5 p-1.5 sm:min-h-28", outside && "bg-black/20 opacity-50")}>
              <div
                className={cn(
                  "grid size-6 place-items-center rounded-full text-xs tabular-nums",
                  key === today ? "bg-ig-gradient font-bold text-white" : "text-muted-foreground",
                )}
              >
                {d.getDate()}
              </div>
              <div className="hidden space-y-1 sm:block">
                {entries.slice(0, 3).map((e) => (
                  <EntryChip key={`${e.kind}-${e.id}`} entry={e} tz={tz} onOpen={onOpen} />
                ))}
                {entries.length > 3 && (
                  <button type="button" onClick={onShowDay} className="px-1.5 text-[11px] text-muted-foreground hover:text-foreground">
                    +{entries.length - 3} more
                  </button>
                )}
              </div>
              {entries.length > 0 && (
                <button type="button" onClick={onShowDay} className="flex flex-wrap gap-1 sm:hidden" aria-label={`${entries.length} entries on ${key}`}>
                  {entries.slice(0, 4).map((e) => (
                    <span key={e.id} className={cn("size-2 rounded-full", statusDot(e.status))} />
                  ))}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
