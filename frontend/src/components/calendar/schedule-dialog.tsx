"use client";

import { useState } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { RightsCheckbox, TimezoneSelect } from "@/components/common/fields";
import { PLATFORM_META } from "@/components/common/brand";
import { useAccounts, useSaveSchedule } from "@/lib/api/hooks/publishing";
import type { AccountOut, ScheduleOut } from "@/lib/api/types";
import { DaysField, FrequencyField, ModeField, type ScheduleDraft } from "./schedule-fields";

const PRIVACY = { public: "Public", unlisted: "Unlisted", private: "Private" };

function initialDraft(s: ScheduleOut | null, accounts: AccountOut[], tz: string): ScheduleDraft {
  return {
    connected_account_id: s?.connected_account_id ?? accounts[0]?.id ?? "",
    name: s?.name ?? "",
    mode: s?.mode ?? "review",
    frequency_type: s?.frequency_type ?? "interval",
    interval_minutes: s?.interval_minutes ?? 180,
    times_of_day: s?.times_of_day ?? [],
    days_of_week: s?.days_of_week ?? [0, 1, 2, 3, 4, 5, 6],
    timezone: s?.timezone ?? tz,
    daily_limit: s?.daily_limit ?? 6,
    window_start: s?.window_start ?? "",
    window_end: s?.window_end ?? "",
    auto_min_score: s?.auto_min_score ?? 70,
    default_privacy: s?.default_privacy ?? "public",
    rights_confirmed: !!s?.rights_confirmed_at,
  };
}

function problem(d: ScheduleDraft): string | null {
  if (!d.connected_account_id) return "Choose an account.";
  if (d.frequency_type === "interval" && (d.interval_minutes < 15 || d.interval_minutes > 10080)) return "The interval must be 15–10080 minutes.";
  if (d.frequency_type === "times" && !d.times_of_day.length) return "Add at least one time of day.";
  if (!d.days_of_week.length) return "Pick at least one day.";
  if (d.daily_limit < 1 || d.daily_limit > 100) return "The daily limit must be 1–100.";
  if (d.window_start && d.window_end && d.window_end <= d.window_start) return "The daily window must end after it starts.";
  // the backend never queues clips on a schedule without a rights confirmation, in either mode
  if (!d.rights_confirmed) return "Confirm your rights to publish; without it nothing is queued.";
  return null;
}

function ScheduleForm({ schedule, accounts, tz, onDone }: { schedule: ScheduleOut | null; accounts: AccountOut[]; tz: string; onDone: () => void }) {
  const save = useSaveSchedule();
  const [draft, setDraft] = useState(() => initialDraft(schedule, accounts, tz));
  const set = (patch: Partial<ScheduleDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const account = accounts.find((a) => a.id === draft.connected_account_id);
  const platform = schedule?.platform ?? account?.platform;
  const error = problem(draft);
  const accountItems = accounts.map((a) => ({ value: a.id, label: `${a.display_name} · ${PLATFORM_META[a.platform]?.label ?? a.platform}` }));

  const submit = () => {
    const { connected_account_id, ...rest } = draft;
    const body = {
      ...rest,
      name: draft.name.trim() || undefined,
      interval_minutes: draft.frequency_type === "interval" ? draft.interval_minutes : null,
      window_start: draft.window_start || null,
      window_end: draft.window_end || null,
      ...(schedule ? {} : { connected_account_id }),
    };
    save.mutate(
      { id: schedule?.id, body },
      {
        onSuccess: () => {
          toast.success(schedule ? "Schedule updated" : "Schedule created");
          onDone();
        },
      },
    );
  };

  return (
    <div className="grid gap-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="sch-account">Account</Label>
          <Select items={accountItems} value={draft.connected_account_id} disabled={!!schedule} onValueChange={(v) => set({ connected_account_id: String(v ?? "") })}>
            <SelectTrigger id="sch-account" className="h-9 w-full rounded-xl">
              <SelectValue placeholder="Choose an account" />
            </SelectTrigger>
            <SelectContent>
              {accountItems.map((i) => (
                <SelectItem key={i.value} value={i.value}>
                  {i.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="sch-name">Name (optional)</Label>
          <Input id="sch-name" value={draft.name} onChange={(e) => set({ name: e.target.value })} maxLength={120} className="h-9 rounded-xl" />
        </div>
      </div>
      <ModeField draft={draft} set={set} />
      {draft.mode === "automatic" && (
        <div className="space-y-2">
          <div className="flex justify-between text-sm">
            <span id="sch-score" className="font-medium">Minimum AI highlight score (estimate)</span>
            <span className="tabular-nums">{draft.auto_min_score}</span>
          </div>
          <Slider className="[&_[data-slot=slider-track]]:bg-white/15" aria-labelledby="sch-score" min={0} max={100} step={5} value={[draft.auto_min_score]} onValueChange={(v) => set({ auto_min_score: Array.isArray(v) ? v[0] : v })} />
        </div>
      )}
      <FrequencyField draft={draft} set={set} />
      <DaysField draft={draft} set={set} />
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="sch-tz">Time zone</Label>
          <TimezoneSelect id="sch-tz" value={draft.timezone} onChange={(timezone) => set({ timezone })} className="rounded-xl" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="sch-limit">Daily limit</Label>
          <Input id="sch-limit" type="number" min={1} max={100} value={draft.daily_limit} onChange={(e) => set({ daily_limit: Number(e.target.value) || 0 })} className="h-9 rounded-xl" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="sch-ws">Daily window (optional)</Label>
          <div className="flex items-center gap-2">
            <Input id="sch-ws" type="time" aria-label="Window start" value={draft.window_start} onChange={(e) => set({ window_start: e.target.value })} className="h-9 rounded-xl" />
            <span className="text-xs text-muted-foreground">to</span>
            <Input type="time" aria-label="Window end" value={draft.window_end} onChange={(e) => set({ window_end: e.target.value })} className="h-9 rounded-xl" />
          </div>
        </div>
        {platform === "youtube" && (
          <div className="space-y-1.5">
            <Label htmlFor="sch-privacy">Default YouTube privacy</Label>
            <Select items={PRIVACY} value={draft.default_privacy} onValueChange={(v) => set({ default_privacy: (v ?? "public") as ScheduleDraft["default_privacy"] })}>
              <SelectTrigger id="sch-privacy" className="h-9 w-full rounded-xl">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(PRIVACY).map(([value, label]) => (
                  <SelectItem key={value} value={value}>
                    {label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
      </div>
      <RightsCheckbox id="sch-rights" checked={draft.rights_confirmed} onChange={(rights_confirmed) => set({ rights_confirmed })} label="I own or am authorised to publish the content this schedule posts" />
      <DialogFooter className="items-center">
        {error && <p className="mr-auto text-xs text-warning">{error}</p>}
        <Button variant="outline" onClick={onDone}>Cancel</Button>
        <Button disabled={!!error || save.isPending} onClick={submit}>
          {save.isPending && <Loader2 className="animate-spin" />} {schedule ? "Save schedule" : "Create schedule"}
        </Button>
      </DialogFooter>
    </div>
  );
}

export function ScheduleDialog({ open, onOpenChange, schedule, tz }: { open: boolean; onOpenChange: (o: boolean) => void; schedule: ScheduleOut | null; tz: string }) {
  const accounts = useAccounts();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="font-heading text-lg">{schedule ? "Edit schedule" : "New schedule"}</DialogTitle>
          <DialogDescription>Decide when an account posts and whether clips are queued automatically.</DialogDescription>
        </DialogHeader>
        {accounts.isLoading ? (
          <Loader2 className="mx-auto my-8 size-5 animate-spin text-muted-foreground" />
        ) : accounts.data?.length ? (
          open && <ScheduleForm key={schedule?.id ?? "new"} schedule={schedule} accounts={accounts.data} tz={tz} onDone={() => onOpenChange(false)} />
        ) : (
          <p className="py-6 text-center text-sm text-muted-foreground">
            Connect a YouTube or Instagram account first. <Link href="/app/accounts" onClick={() => onOpenChange(false)} className="font-medium text-foreground underline underline-offset-4">Connected Accounts</Link>
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}
