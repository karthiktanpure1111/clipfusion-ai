"use client";

import { useState } from "react";
import Link from "next/link";
import { CalendarClock, ExternalLink, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { PLATFORM_META, PlatformIcon } from "@/components/common/brand";
import { StatusBadge } from "@/components/common/status-badge";
import { VideoThumb } from "@/components/common/video-thumb";
import { useNow } from "@/components/common/use-now";
import { localInputToIso } from "@/components/clips/publish-dialog";
import { useQueue, useUpdateQueueItem } from "@/lib/api/hooks/publishing";
import type { CalendarEntry, QueueStatus } from "@/lib/api/types";
import { formatDateTime, formatInTz } from "@/lib/format";
import { cn } from "@/lib/utils";
import { EDITABLE, QueueActions } from "./queue-item";

const INPUT_FORMAT = "yyyy-MM-dd'T'HH:mm";

function Reschedule({ id, at, tz }: { id: string; at: string; tz: string }) {
  const update = useUpdateQueueItem();
  const now = useNow();
  const [value, setValue] = useState(() => formatInTz(at, INPUT_FORMAT, tz));
  const iso = localInputToIso(value, tz);
  const past = !!iso && now > 0 && new Date(iso).getTime() <= now;
  return (
    <div className="space-y-2 rounded-xl border border-white/8 p-3">
      <Label htmlFor="reschedule">Reschedule ({tz})</Label>
      <div className="flex gap-2">
        <Input id="reschedule" type="datetime-local" value={value} onChange={(e) => setValue(e.target.value)} className="h-9 flex-1 rounded-xl" aria-invalid={past || undefined} />
        <Button
          className="h-9"
          disabled={!iso || past || update.isPending}
          onClick={() => update.mutate({ id, scheduled_at: iso }, { onSuccess: () => toast.success("Rescheduled") })}
        >
          {update.isPending ? <Loader2 className="animate-spin" /> : <CalendarClock />} Save
        </Button>
      </div>
      {past && <p className="text-xs text-[#ff5c7a]">Pick a time in the future.</p>}
    </div>
  );
}

function EntryBody({ entry, tz, onClose }: { entry: CalendarEntry; tz: string; onClose: () => void }) {
  const isQueue = entry.kind === "queue";
  const queue = useQueue({ clip_id: entry.clip_id ?? undefined }, isQueue && !!entry.clip_id);
  const live = isQueue ? queue.data?.find((q) => q.id === entry.id) : undefined;
  const status = live?.status ?? entry.status;
  const at = live?.published_at ?? live?.scheduled_at ?? entry.at;
  const url = live?.external_url ?? entry.external_url;
  const editable = isQueue && EDITABLE.includes(status as QueueStatus);

  return (
    <div className="space-y-4 overflow-y-auto px-4 pb-6">
      <VideoThumb src={entry.thumbnail_url} alt="" className="rounded-xl" />
      <div className="space-y-1.5">
        <p className="font-medium">{entry.clip_title || "Untitled clip"}</p>
        <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <PlatformIcon platform={entry.platform} /> {PLATFORM_META[entry.platform]?.product ?? entry.platform} · {entry.account_name}
        </p>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <StatusBadge status={status} />
          <span className="text-muted-foreground">{formatDateTime(at, tz)}</span>
        </div>
      </div>
      {live?.last_error && <p className="rounded-xl bg-destructive/8 p-3 text-xs text-[#ff8098]">{live.last_error}</p>}
      {editable && <Reschedule key={`${at}-${tz}`} id={entry.id} at={at} tz={tz} />}
      {isQueue && <QueueActions id={entry.id} status={status} />}
      <div className="flex flex-wrap gap-2">
        {entry.clip_id && (
          <Link href={`/app/clips/${entry.clip_id}`} onClick={onClose} className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
            Open clip
          </Link>
        )}
        {url && (
          <a href={url} target="_blank" rel="noopener noreferrer" className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
            <ExternalLink /> View on {PLATFORM_META[entry.platform]?.label ?? "platform"}
          </a>
        )}
      </div>
    </div>
  );
}

export function EntryDrawer({ entry, tz, onClose }: { entry: CalendarEntry | null; tz: string; onClose: () => void }) {
  return (
    <Sheet open={!!entry} onOpenChange={(o) => !o && onClose()}>
      <SheetContent side="right" className="w-full gap-2 bg-[#14141a] sm:max-w-md">
        <SheetHeader className="px-4 pt-4">
          <SheetTitle className="font-heading">Publication</SheetTitle>
          <SheetDescription>Reschedule, publish now or cancel this post.</SheetDescription>
        </SheetHeader>
        {entry && <EntryBody key={`${entry.kind}-${entry.id}`} entry={entry} tz={tz} onClose={onClose} />}
      </SheetContent>
    </Sheet>
  );
}
