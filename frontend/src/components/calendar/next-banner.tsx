"use client";

import { Loader2, Pause, Play } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { PlatformIcon } from "@/components/common/brand";
import { Countdown } from "@/components/common/fields";
import { ErrorState, Surface } from "@/components/common/page";
import { useNextPublish, usePublishingPause } from "@/lib/api/hooks/publishing";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Next publishing time (live countdown), per-account slots and pause/resume-all. */
export function NextBanner({ tz }: { tz: string }) {
  const { data, error, refetch } = useNextPublish();
  const pause = usePublishingPause();
  const paused = data?.paused ?? false;

  if (error) return <ErrorState error={error} onRetry={() => refetch()} className="mb-5 py-6" />;

  return (
    <Surface
      className={cn(
        "mb-5 flex flex-col gap-4 p-4 sm:flex-row sm:items-center sm:justify-between sm:p-5",
        paused ? "border-warning/30 bg-warning/5" : "gradient-border",
      )}
    >
      <div>
        <p className="text-xs tracking-wide text-muted-foreground uppercase">Next publishing time</p>
        {!data ? (
          <Skeleton className="mt-2 h-7 w-48" />
        ) : paused ? (
          <p className="mt-1 font-heading text-xl font-bold text-warning">Publishing is paused</p>
        ) : data.next_publish_at ? (
          <p className="mt-1 font-heading text-xl font-bold">
            <Countdown to={data.next_publish_at} />{" "}
            <span className="text-sm font-medium text-muted-foreground">· {formatDateTime(data.next_publish_at, tz)}</span>
          </p>
        ) : (
          <p className="mt-1 text-sm text-muted-foreground">Nothing scheduled yet.</p>
        )}
        {!!data?.accounts.length && (
          <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            {data.accounts.map((a) => (
              <li key={a.account_id} className="flex items-center gap-1.5">
                <PlatformIcon platform={a.platform} className="size-3.5" /> {a.display_name}:{" "}
                {a.next_publish_at
                  ? formatDateTime(a.next_publish_at, tz)
                  : a.next_free_slot
                    ? `free slot ${formatDateTime(a.next_free_slot, tz)}`
                    : "no schedule"}
              </li>
            ))}
          </ul>
        )}
      </div>
      <Button
        variant={paused ? "default" : "outline"}
        size="lg"
        className="rounded-xl"
        disabled={!data || pause.isPending}
        onClick={() => pause.mutate(!paused, { onSuccess: () => toast.success(paused ? "Publishing resumed" : "All publishing paused") })}
      >
        {pause.isPending ? <Loader2 className="animate-spin" /> : paused ? <Play /> : <Pause />}
        {paused ? "Resume publishing" : "Pause all"}
      </Button>
    </Surface>
  );
}
