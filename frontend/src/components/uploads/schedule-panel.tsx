"use client";

import { useState } from "react";
import { CalendarClock, CalendarX2, Loader2, Repeat, UploadCloud, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { useNow } from "@/components/common/use-now";
import { localInputToIso } from "@/components/clips/publish-dialog";
import { useUserTz } from "@/lib/api/hooks/auth";
import { useAutoSchedule, useClearTimeline, useSaveAutoSchedule, useScheduledItemAction, useScheduleTimeline, useTimelineNext } from "@/lib/api/hooks/timeline";
import type { ClipOut } from "@/lib/api/types";
import { browserTimezone, clipNumber, formatInTz } from "@/lib/format";
import { cn } from "@/lib/utils";
import { canUploadWith, type UploadTarget } from "./upload-controls";

export const GAP_ITEMS = [
  { value: "15", label: "15 minutes" },
  { value: "30", label: "30 minutes" },
  { value: "60", label: "1 hour" },
  { value: "120", label: "2 hours" },
  { value: "180", label: "3 hours" },
  { value: "360", label: "6 hours" },
  { value: "720", label: "12 hours" },
  { value: "1440", label: "24 hours" },
];

const START_ITEMS = [
  { value: "asap", label: "As soon as possible" },
  { value: "time", label: "At a time I pick" },
];

export const gapLabel = (m: number) => (m % 60 === 0 ? `${m / 60} hour${m === 60 ? "" : "s"}` : `${m} min`);

export const slotText = (iso: string | null | undefined, tz: string) => formatInTz(iso, "EEE d MMM · HH:mm", tz);

/** "in 5 min", "in 2 h 10 min", "in 3 days" (empty during SSR). */
export function inText(iso: string | null | undefined, now: number): string {
  if (!iso || !now) return "";
  const mins = Math.round((new Date(iso).getTime() - now) / 60_000);
  if (mins <= 1) return "right now";
  if (mins < 60) return `in ${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (h < 48) return `in ${h} h${m ? ` ${m} min` : ""}`;
  return `in ${Math.round(h / 24)} days`;
}

const addMinutes = (iso: string, minutes: number) => new Date(new Date(iso).getTime() + minutes * 60_000).toISOString();

/** Plan and schedule the video's ready clips one by one, `gap` minutes apart. */
export function SchedulePlanner({
  sourceId,
  ready,
  target,
  rights,
  gap,
  onGapChange,
}: {
  sourceId: string;
  ready: ClipOut[];
  target: UploadTarget;
  rights: boolean;
  gap: number;
  onGapChange: (m: number) => void;
}) {
  const tz = useUserTz() ?? browserTimezone();
  const now = useNow();
  const [start, setStart] = useState<"asap" | "time">("asap");
  const [time, setTime] = useState("");
  const next = useTimelineNext(target.accountId, gap, Math.min(ready.length, 200));
  const schedule = useScheduleTimeline();
  const customIso = start === "time" ? localInputToIso(time, tz) : null;
  const first = start === "time" ? customIso : (next.data?.next_at ?? null);
  const inPast = start === "time" && !!customIso && !!now && new Date(customIso).getTime() <= now;
  const can = canUploadWith(target.account) && rights && ready.length > 0 && !!first && !inPast && !schedule.isPending;
  // "as soon as possible": the server's plan (uses free time, never within a gap of another scheduled post)
  const slots = start === "asap" ? next.data?.slots : undefined;
  const plan = first ? ready.map((c, i) => ({ clip: c, at: slots?.[i] ?? addMinutes(first, i * gap) })) : [];
  const shown = plan.slice(0, 5);

  const submit = async () => {
    if (!target.accountId) return;
    const res = await schedule
      .mutateAsync({
        video_source_id: sourceId,
        connected_account_id: target.accountId,
        gap_minutes: gap,
        start_at: customIso,
        privacy: target.privacy,
        rights_confirmed: true,
      })
      .catch(() => null);
    if (res)
      toast.success(`Scheduled ${res.scheduled} clip${res.scheduled === 1 ? "" : "s"}, ${gapLabel(gap)} apart`, {
        description: res.first_at ? `First ${slotText(res.first_at, tz)}${res.scheduled > 1 ? ` · last ${slotText(res.last_at, tz)}` : ""}` : undefined,
      });
  };

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={`gap-${sourceId}`}>Time between posts</Label>
          <Select items={GAP_ITEMS} value={String(gap)} onValueChange={(v) => onGapChange(Number(v ?? 60))}>
            <SelectTrigger id={`gap-${sourceId}`} className="h-9 w-full rounded-xl">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {GAP_ITEMS.map((g) => (
                <SelectItem key={g.value} value={g.value}>
                  {g.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`start-${sourceId}`}>First post</Label>
          <Select items={START_ITEMS} value={start} onValueChange={(v) => setStart((v ?? "asap") as "asap" | "time")}>
            <SelectTrigger id={`start-${sourceId}`} className="h-9 w-full rounded-xl">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {START_ITEMS.map((s) => (
                <SelectItem key={s.value} value={s.value}>
                  {s.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
      {start === "time" && (
        <div className="space-y-1">
          <Input
            type="datetime-local"
            value={time}
            onChange={(e) => setTime(e.target.value)}
            className="h-9 w-full rounded-xl sm:w-64"
            aria-label="First post time"
          />
          <p className="text-xs text-muted-foreground">
            Time zone: {tz}
            {inPast && <span className="text-[#ff5c7a]"> · pick a time in the future</span>}
          </p>
        </div>
      )}
      {start === "asap" && first && (
        <p className="text-xs text-muted-foreground">
          {now && new Date(first).getTime() - now <= 90_000
            ? `Clip ${clipNumber(ready[0]?.sequence).replace(/^Clip /, "")} uploads right now, then one clip every ${gapLabel(gap)}.`
            : `The first clip goes out ${inText(first, now)} — ${gapLabel(gap)} after the last post already waiting on this channel.`}
        </p>
      )}

      {ready.length > 0 && plan.length > 0 && (
        <ol className="space-y-1 rounded-xl border border-white/8 bg-black/15 p-2.5" aria-label="Planned upload times">
          {shown.map(({ clip, at }) => (
            <li key={clip.id} className="flex items-center gap-2 text-xs">
              <span className="w-14 shrink-0 font-mono whitespace-nowrap text-muted-foreground">{clipNumber(clip.sequence)}</span>
              <span className="min-w-0 flex-1 truncate">{clip.social_metadata?.youtube.title || clip.title || "Untitled clip"}</span>
              <span className="shrink-0 tabular-nums text-foreground/85">{slotText(at, tz)}</span>
            </li>
          ))}
          {plan.length > shown.length && (
            <li className="text-xs text-muted-foreground">
              +{plan.length - shown.length} more · last at {slotText(plan[plan.length - 1].at, tz)}
            </li>
          )}
        </ol>
      )}

      <Button className="glow-red rounded-xl" disabled={!can} onClick={submit}>
        {schedule.isPending ? <Loader2 className="animate-spin" /> : <CalendarClock />}
        {ready.length ? `Schedule ${ready.length} clip${ready.length === 1 ? "" : "s"} · ${gapLabel(gap)} apart` : "Nothing left to schedule"}
      </Button>
      <p className="text-[11px] leading-relaxed text-muted-foreground">
        Uploads run from this computer, so keep ClipFusion running. If it was off at a scheduled time, the missed posts move later together and keep their
        spacing — they never go out all at once.
      </p>
    </div>
  );
}

/** The video's clips that are scheduled, with Upload now / Unschedule. */
export function ScheduledList({ sourceId, clips }: { sourceId: string; clips: ClipOut[] }) {
  const tz = useUserTz() ?? browserTimezone();
  const now = useNow();
  const action = useScheduledItemAction();
  const clear = useClearTimeline();
  const scheduled = clips
    .filter((c) => c.scheduled_at && c.scheduled_item_id)
    .sort((a, b) => (a.scheduled_at as string).localeCompare(b.scheduled_at as string));
  if (!scheduled.length) return null;
  return (
    <div className="space-y-2 rounded-xl border border-info/20 bg-info/[0.04] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-sm font-medium">
          <CalendarClock className="size-4 text-info" /> Scheduled ({scheduled.length})
        </p>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 rounded-lg text-xs text-muted-foreground"
          disabled={clear.isPending}
          onClick={() =>
            clear.mutate(sourceId, {
              onSuccess: (r) => toast.info(`Cleared ${r.cancelled} scheduled upload${r.cancelled === 1 ? "" : "s"}`),
            })
          }
        >
          <CalendarX2 /> Clear schedule
        </Button>
      </div>
      <ol className="max-h-72 space-y-1.5 overflow-y-auto pr-1">
        {scheduled.map((c) => (
          <li key={c.id} className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-white/6 bg-black/10 px-2.5 py-1.5 text-xs">
            <span className="w-14 shrink-0 font-mono whitespace-nowrap text-muted-foreground">{clipNumber(c.sequence)}</span>
            <span className="min-w-0 flex-1 truncate text-sm">{c.social_metadata?.youtube.title || c.title || "Untitled clip"}</span>
            <span className="shrink-0 tabular-nums" title={inText(c.scheduled_at, now)}>
              {slotText(c.scheduled_at, tz)} <span className="text-muted-foreground">· {inText(c.scheduled_at, now)}</span>
            </span>
            <span className="flex shrink-0 gap-1">
              <Button
                size="sm"
                variant="outline"
                className="h-6 rounded-md px-2 text-[11px]"
                disabled={action.isPending}
                onClick={() =>
                  action.mutate(
                    {
                      id: c.scheduled_item_id as string,
                      action: "publish-now",
                    },
                    {
                      onSuccess: () => toast.success(`Uploading ${clipNumber(c.sequence)} now`),
                    },
                  )
                }
              >
                <UploadCloud className="size-3" /> Upload now
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 rounded-md px-2 text-[11px] text-muted-foreground"
                disabled={action.isPending}
                onClick={() =>
                  action.mutate(
                    { id: c.scheduled_item_id as string, action: "cancel" },
                    {
                      onSuccess: () => toast.info(`${clipNumber(c.sequence)} unscheduled`),
                    },
                  )
                }
              >
                <X className="size-3" /> Unschedule
              </Button>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** Upload on render (on by default): each clip the user renders uploads right away or `gap` after the previous clip. */
export function AutoScheduleToggle({ target, gap }: { target: UploadTarget; gap: number }) {
  const tz = useUserTz() ?? browserTimezone();
  const now = useNow();
  const auto = useAutoSchedule();
  const save = useSaveAutoSchedule();
  const a = auto.data;
  const on = !!a?.enabled;
  const canEnable = canUploadWith(target.account);
  const differs =
    on && !!target.accountId && (a?.connected_account_id !== target.accountId || a?.gap_minutes !== gap || (a?.privacy ?? target.privacy) !== target.privacy);

  const apply = (enabled: boolean) =>
    save.mutate(
      {
        enabled,
        connected_account_id: target.accountId,
        gap_minutes: gap,
        privacy: target.privacy,
      },
      {
        onSuccess: (r) =>
          r.enabled
            ? toast.success(`On: each clip you render uploads right away or ${gapLabel(r.gap_minutes)} after the previous one`)
            : toast.info("Off: rendering a clip no longer uploads it"),
      },
    );

  return (
    <div className={cn("flex gap-3 rounded-xl border p-3", on ? "border-success/25 bg-success/[0.04]" : "border-white/8 bg-black/15")}>
      <Switch
        checked={on}
        disabled={auto.isLoading || save.isPending || (!on && !canEnable)}
        onCheckedChange={(v) => apply(!!v)}
        aria-label="Upload each clip I render"
      />
      <div className="min-w-0 space-y-1 text-sm">
        <p className="flex items-center gap-1.5 font-medium">
          <Repeat className="size-3.5" /> Upload each clip I render
        </p>
        <p className="text-xs leading-relaxed text-muted-foreground">
          Clips are not rendered automatically — press Render on the ones you want. Each clip you render goes to YouTube as soon as it is ready: right away, or{" "}
          {gapLabel(on && a ? a.gap_minutes : gap)} after the previous clip, so they go out one by one in the order you render them. It uses the rights you
          confirmed when adding the video. For clips that were rendered earlier, use the Schedule button.
        </p>
        {on && a && (
          <p className="text-xs text-success">
            On · {a.account_name ?? "YouTube"} · {gapLabel(a.gap_minutes)} apart · {a.privacy ?? "default privacy"}
            {a.next_slot_at && (
              <span className="text-muted-foreground">
                {" "}
                · next free slot {slotText(a.next_slot_at, tz)} ({inText(a.next_slot_at, now)})
              </span>
            )}
          </p>
        )}
        {differs && (
          <Button size="sm" variant="outline" className="h-7 rounded-lg text-xs" disabled={save.isPending} onClick={() => apply(true)}>
            Use the account, privacy and {gapLabel(gap)} gap chosen above
          </Button>
        )}
        {!on && !canEnable && <p className="text-xs text-muted-foreground">Connect a YouTube account that can upload to turn it on.</p>}
      </div>
    </div>
  );
}
