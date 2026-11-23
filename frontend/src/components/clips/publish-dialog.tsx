"use client";

import { useState } from "react";
import Link from "next/link";
import { TZDate } from "@date-fns/tz";
import { Check, Loader2, Send } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PlatformIcon } from "@/components/common/brand";
import { RightsCheckbox } from "@/components/common/fields";
import { Segmented } from "@/components/common/segmented";
import { useNow } from "@/components/common/use-now";
import { useUserTz } from "@/lib/api/hooks/auth";
import { useAccounts, useCreateQueue, useNextPublish } from "@/lib/api/hooks/publishing";
import type { ClipDetail } from "@/lib/api/types";
import { browserTimezone, formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/** "2026-11-22T18:30" interpreted in `tz` -> ISO UTC string. */
export function localInputToIso(value: string, tz: string): string | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  if (!m) return null;
  const d = new TZDate(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], 0, tz);
  return new Date(d.getTime()).toISOString();
}

function Preview({ clip, platform }: { clip: ClipDetail; platform: string }) {
  const yt = clip.social_metadata.youtube;
  const ig = clip.social_metadata.instagram;
  return (
    <div className="space-y-1 rounded-xl bg-black/25 p-3 text-xs">
      <div className="flex items-center gap-1.5 font-medium">
        <PlatformIcon platform={platform} className="size-3.5" /> {platform === "youtube" ? "YouTube Shorts payload" : "Instagram Reels payload"}
      </div>
      {platform === "youtube" ? (
        <>
          <p><span className="text-muted-foreground">Title:</span> {yt.title || clip.title || "—"}</p>
          <p className="line-clamp-2"><span className="text-muted-foreground">Description:</span> {yt.description || "—"}</p>
          <p><span className="text-muted-foreground">Privacy:</span> {yt.privacy} · made for kids: {yt.made_for_kids ? "yes" : "no"}</p>
          {yt.hashtags.length > 0 && <p className="text-muted-foreground">{yt.hashtags.map((h) => `#${h}`).join(" ")}</p>}
        </>
      ) : (
        <>
          <p className="line-clamp-3"><span className="text-muted-foreground">Caption:</span> {ig.caption || clip.title || "—"}</p>
          {ig.hashtags.length > 0 && <p className="text-muted-foreground">{ig.hashtags.map((h) => `#${h}`).join(" ")}</p>}
          <p><span className="text-muted-foreground">Share to feed:</span> {ig.share_to_feed ? "yes" : "no"}</p>
        </>
      )}
    </div>
  );
}

function PublishForm({ clip, onDone }: { clip: ClipDetail; onDone: () => void }) {
  const tz = useUserTz() ?? browserTimezone();
  const now = useNow();
  const accounts = useAccounts();
  const next = useNextPublish();
  const create = useCreateQueue();
  const usable = (accounts.data ?? []).filter((a) => a.status === "active" && a.can_publish);
  const [selected, setSelected] = useState<string[]>([]);
  const [when, setWhen] = useState<"slot" | "time">("slot");
  const [time, setTime] = useState("");
  const [rights, setRights] = useState(false);
  const iso = when === "time" ? localInputToIso(time, tz) : null;
  const platforms = [...new Set(usable.filter((a) => selected.includes(a.id)).map((a) => a.platform))];
  const valid = selected.length > 0 && rights && (when === "slot" || (!!iso && new Date(iso).getTime() > now));

  const submit = async () => {
    const items = await create.mutateAsync({ clip_id: clip.id, account_ids: selected, scheduled_at: iso, rights_confirmed: true }).catch(() => null);
    if (!items) return; // MutationCache already showed the error
    toast.success(`Scheduled ${items.length} publication${items.length === 1 ? "" : "s"}`, {
      description: items[0]?.scheduled_at ? `First at ${formatDateTime(items[0].scheduled_at, tz)}` : undefined,
    });
    onDone();
  };

  if (accounts.isLoading) return <Loader2 className="mx-auto my-8 size-5 animate-spin text-muted-foreground" />;
  if (!usable.length)
    return (
      <div className="space-y-3 py-4 text-center text-sm text-muted-foreground">
        <p>No connected account can publish yet. Connect YouTube or an Instagram professional account first.</p>
        <Link href="/app/accounts" onClick={onDone} className="font-medium text-foreground underline underline-offset-4">Go to Connected Accounts</Link>
      </div>
    );

  return (
    <div className="grid gap-4">
      <fieldset>
        <legend className="mb-2 text-sm font-medium">Publish to</legend>
        <div className="grid gap-2">
          {usable.map((a) => {
            const on = selected.includes(a.id);
            const slot = next.data?.accounts.find((x) => x.account_id === a.id)?.next_free_slot;
            return (
              <button
                key={a.id}
                type="button"
                aria-pressed={on}
                onClick={() => setSelected((s) => (on ? s.filter((x) => x !== a.id) : [...s, a.id]))}
                className={cn(
                  "flex items-center gap-3 rounded-xl border p-3 text-left transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                  on ? "border-ig-pink/50 bg-ig-pink/5" : "border-white/10 hover:bg-white/4",
                )}
              >
                <PlatformIcon platform={a.platform} className="size-5" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{a.display_name}</span>
                  <span className="block text-xs text-muted-foreground">{slot ? `Next free slot ${formatDateTime(slot, tz)}` : "No schedule — defaults to about 5 minutes from now"}</span>
                </span>
                <span className={cn("grid size-5 place-items-center rounded-md ring-1", on ? "bg-primary ring-primary" : "ring-white/20")}>{on && <Check className="size-3.5" />}</span>
              </button>
            );
          })}
        </div>
      </fieldset>
      <div className="space-y-2">
        <Label>When</Label>
        <Segmented ariaLabel="When to publish" size="sm" value={when} onChange={setWhen} options={[{ value: "slot", label: "Next free slot" }, { value: "time", label: "Specific time" }]} />
        {when === "time" && (
          <div className="space-y-1">
            <Input type="datetime-local" value={time} onChange={(e) => setTime(e.target.value)} className="h-9 w-full rounded-xl sm:w-64" aria-label="Publish time" />
            <p className="text-xs text-muted-foreground">Time zone: {tz}{iso && new Date(iso).getTime() <= now && <span className="text-[#ff5c7a]"> · pick a time in the future</span>}</p>
          </div>
        )}
      </div>
      {platforms.map((p) => <Preview key={p} clip={clip} platform={p} />)}
      {clip.render_status !== "rendered" && (
        <p className="text-xs text-muted-foreground">The clip isn&apos;t rendered (or the render is outdated); it will be rendered automatically before upload.</p>
      )}
      <RightsCheckbox id={`rights-publish-${clip.id}`} checked={rights} onChange={setRights} />
      <DialogFooter>
        <Button variant="outline" onClick={onDone}>Cancel</Button>
        <Button className="glow-red" disabled={!valid || create.isPending} onClick={submit}>
          {create.isPending ? <Loader2 className="animate-spin" /> : <Send />} Schedule
        </Button>
      </DialogFooter>
    </div>
  );
}

export function PublishDialog({ clip, open, onOpenChange }: { clip: ClipDetail; open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-heading text-lg">Publish or schedule</DialogTitle>
          <DialogDescription>Uploads go through the official YouTube Data API and Instagram Graph API.</DialogDescription>
        </DialogHeader>
        {open && <PublishForm clip={clip} onDone={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}
