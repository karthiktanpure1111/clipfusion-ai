"use client";

import { format, parseISO } from "date-fns";
import { CalendarX2 } from "lucide-react";
import { PlatformIcon } from "@/components/common/brand";
import { EmptyState } from "@/components/common/page";
import { StatusBadge } from "@/components/common/status-badge";
import { VideoThumb } from "@/components/common/video-thumb";
import type { CalendarEntry } from "@/lib/api/types";
import { formatTime } from "@/lib/format";

export function AgendaList({ byDay, tz, onOpen }: { byDay: Map<string, CalendarEntry[]>; tz: string; onOpen: (e: CalendarEntry) => void }) {
  const days = [...byDay.keys()].sort();
  if (!days.length)
    return <EmptyState icon={CalendarX2} title="Nothing in this period" description="Schedule clips or create a publishing schedule to fill the calendar." />;
  return (
    <div className="space-y-5">
      {days.map((day) => (
        <section key={day} aria-label={day}>
          <h3 className="mb-2 font-heading text-sm font-semibold">{format(parseISO(day), "EEEE, MMMM d")}</h3>
          <ul className="space-y-2">
            {byDay.get(day)!.map((e) => (
              <li key={`${e.kind}-${e.id}`}>
                <button
                  type="button"
                  onClick={() => onOpen(e)}
                  className="flex w-full items-center gap-3 rounded-2xl border border-white/7 bg-card/60 p-2.5 text-left transition-colors hover:border-white/15 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                >
                  <span className="w-12 shrink-0 text-center text-sm font-medium tabular-nums">{formatTime(e.at, tz)}</span>
                  <VideoThumb src={e.thumbnail_url} alt="" vertical className="w-8 shrink-0 rounded-md" />
                  <span className="min-w-0 flex-1">
                    <span className="line-clamp-1 text-sm font-medium">{e.clip_title || "Untitled clip"}</span>
                    <span className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
                      <PlatformIcon platform={e.platform} className="size-3.5" /> {e.account_name}
                    </span>
                  </span>
                  <StatusBadge status={e.status} />
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
