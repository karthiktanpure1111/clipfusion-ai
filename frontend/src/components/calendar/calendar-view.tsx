"use client";

import { useMemo, useState } from "react";
import { addDays, addMonths, format, startOfMonth, startOfWeek } from "date-fns";
import { TZDate } from "@date-fns/tz";
import { CalendarDays, ChevronLeft, ChevronRight, List } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState, PageHeader } from "@/components/common/page";
import { Segmented } from "@/components/common/segmented";
import { useUserTz } from "@/lib/api/hooks/auth";
import { useCalendar } from "@/lib/api/hooks/publishing";
import type { CalendarEntry } from "@/lib/api/types";
import { browserTimezone, formatInTz } from "@/lib/format";
import { AgendaList } from "./agenda-list";
import { EntryDrawer } from "./entry-drawer";
import { MonthGrid } from "./month-grid";
import { NextBanner } from "./next-banner";
import { SchedulesPanel } from "./schedules-panel";

const CELLS = 42;

export function CalendarView() {
  const tz = useUserTz() ?? browserTimezone();
  const [month, setMonth] = useState(() => startOfMonth(new Date()));
  const today = useMemo(() => formatInTz(new Date(), "yyyy-MM-dd", tz), [tz]);
  const [view, setView] = useState<"month" | "agenda">("month");
  const [entry, setEntry] = useState<CalendarEntry | null>(null);

  const { days, startIso, endIso } = useMemo(() => {
    const first = startOfWeek(month, { weekStartsOn: 1 });
    const cells = Array.from({ length: CELLS }, (_, i) => addDays(first, i));
    const at = (d: Date) => new Date(new TZDate(d.getFullYear(), d.getMonth(), d.getDate(), 0, 0, 0, tz).getTime()).toISOString();
    return { days: cells, startIso: at(cells[0]), endIso: at(addDays(cells[CELLS - 1], 1)) };
  }, [month, tz]);

  const { data, isLoading, error, refetch } = useCalendar(startIso, endIso);
  const byDay = useMemo(() => {
    const map = new Map<string, CalendarEntry[]>();
    for (const e of data ?? []) {
      const key = formatInTz(e.at, "yyyy-MM-dd", tz);
      map.set(key, [...(map.get(key) ?? []), e]);
    }
    return map;
  }, [data, tz]);
  const agenda = useMemo(() => new Map([...byDay].filter(([k]) => k.startsWith(format(month, "yyyy-MM")))), [byDay, month]);

  return (
    <>
      <PageHeader title="Publishing Calendar" description={`Everything queued and published, shown in your time zone (${tz}).`} />
      <NextBanner tz={tz} />
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-1">
          <Button variant="outline" size="icon" aria-label="Previous month" onClick={() => setMonth((m) => addMonths(m, -1))}>
            <ChevronLeft />
          </Button>
          <Button variant="outline" size="icon" aria-label="Next month" onClick={() => setMonth((m) => addMonths(m, 1))}>
            <ChevronRight />
          </Button>
          <h2 className="ml-2 font-heading text-lg font-semibold" aria-live="polite">
            {format(month, "MMMM yyyy")}
          </h2>
          <Button variant="ghost" size="sm" onClick={() => setMonth(startOfMonth(new Date()))}>
            Today
          </Button>
        </div>
        <Segmented
          ariaLabel="Calendar view"
          value={view}
          onChange={setView}
          options={[
            { value: "month", label: <><CalendarDays className="size-4" /> Month</> },
            { value: "agenda", label: <><List className="size-4" /> Agenda</> },
          ]}
        />
      </div>
      {error ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading ? (
        <Skeleton className="h-[560px] rounded-2xl" />
      ) : view === "month" ? (
        <MonthGrid days={days} month={month.getMonth()} today={today} byDay={byDay} tz={tz} onOpen={setEntry} onShowDay={() => setView("agenda")} />
      ) : (
        <AgendaList byDay={agenda} tz={tz} onOpen={setEntry} />
      )}
      <SchedulesPanel tz={tz} />
      <EntryDrawer entry={entry} tz={tz} onClose={() => setEntry(null)} />
    </>
  );
}
