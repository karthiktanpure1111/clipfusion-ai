"use client";

import { Ban, ExternalLink, RotateCcw, Send } from "lucide-react";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { PlatformIcon } from "@/components/common/brand";
import { StatusBadge } from "@/components/common/status-badge";
import { useUserTz } from "@/lib/api/hooks/auth";
import { useQueueAction } from "@/lib/api/hooks/publishing";
import type { QueueItemOut, QueueStatus } from "@/lib/api/types";
import { formatDateTime, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

// Mirrors backend/app/modules/publishing/service.py
export const EDITABLE: QueueStatus[] = ["pending_approval", "scheduled", "rendering", "failed"];
export const CANCELLABLE: QueueStatus[] = EDITABLE;
export const PUBLISHABLE_NOW: QueueStatus[] = ["scheduled", "pending_approval"];
export const RETRYABLE: QueueStatus[] = ["failed", "cancelled"];

export function QueueActions({ id, status, className }: { id: string; status: QueueStatus | string; className?: string }) {
  const action = useQueueAction();
  const s = status as QueueStatus;
  const run = (a: "cancel" | "retry" | "publish-now", msg: string) =>
    action.mutate({ id, action: a }, { onSuccess: () => toast.success(msg) });
  return (
    <div className={cn("flex flex-wrap gap-1.5", className)}>
      {PUBLISHABLE_NOW.includes(s) && (
        <Button size="sm" disabled={action.isPending} onClick={() => run("publish-now", "Publishing started")}>
          <Send /> Publish now
        </Button>
      )}
      {RETRYABLE.includes(s) && (
        <Button size="sm" variant="outline" disabled={action.isPending} onClick={() => run("retry", "Queued again")}>
          <RotateCcw /> Retry
        </Button>
      )}
      {CANCELLABLE.includes(s) && (
        <Button size="sm" variant="ghost" disabled={action.isPending} onClick={() => run("cancel", "Publication cancelled")}>
          <Ban /> Cancel
        </Button>
      )}
    </div>
  );
}

export function QueueItemRow({ item }: { item: QueueItemOut }) {
  const tz = useUserTz();
  return (
    <li className="space-y-2 rounded-xl border border-white/7 bg-black/15 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <PlatformIcon platform={item.platform} />
        <span className="min-w-0 flex-1 truncate text-sm font-medium">{item.account_name}</span>
        <StatusBadge status={item.status} />
      </div>
      <p className="text-xs text-muted-foreground">
        {item.published_at
          ? `Published ${formatDateTime(item.published_at, tz)}`
          : item.scheduled_at
            ? `Scheduled ${formatDateTime(item.scheduled_at, tz)} (${formatRelative(item.scheduled_at)})`
            : "Not scheduled yet"}
        {item.attempts > 1 && ` · attempt ${item.attempts}/${item.max_attempts}`}
        {item.remote_status && ` · platform status: ${item.remote_status}`}
      </p>
      {item.last_error && <p className="rounded-lg bg-destructive/8 p-2 text-xs text-[#ff8098]">{item.last_error}</p>}
      <div className="flex flex-wrap items-center gap-1.5">
        {item.external_url && (
          <a href={item.external_url} target="_blank" rel="noopener noreferrer" className={cn(buttonVariants({ variant: "outline", size: "sm" }))}>
            <ExternalLink /> View post
          </a>
        )}
        <QueueActions id={item.id} status={item.status} />
      </div>
    </li>
  );
}
