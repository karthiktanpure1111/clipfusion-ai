"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Bell, BellOff, CheckCheck, CheckCircle2, Clapperboard, Info, Send, UserCog } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useMarkNotification, useNotifications } from "@/lib/api/hooks/settings";
import type { NotificationOut } from "@/lib/api/types";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

const ICONS: Record<string, { icon: typeof Bell; className: string }> = {
  processing_complete: { icon: CheckCircle2, className: "text-success" },
  processing_failed: { icon: AlertTriangle, className: "text-[#ff5c7a]" },
  render_complete: { icon: Clapperboard, className: "text-success" },
  render_failed: { icon: AlertTriangle, className: "text-[#ff5c7a]" },
  publish_success: { icon: Send, className: "text-success" },
  publish_failed: { icon: AlertTriangle, className: "text-[#ff5c7a]" },
  account_issue: { icon: UserCog, className: "text-warning" },
  clips_awaiting_approval: { icon: Clapperboard, className: "text-info" },
};

/** Backend links may be absolute frontend URLs or app paths. */
function toAppPath(link: string): string {
  try {
    const u = new URL(link, window.location.origin);
    return u.origin === window.location.origin ? u.pathname + u.search : link;
  } catch {
    return link;
  }
}

export function NotificationsBell() {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const { data, isLoading } = useNotifications();
  const mark = useMarkNotification();
  const unread = data?.unread_count ?? 0;

  const openItem = (n: NotificationOut) => {
    if (!n.read_at) mark.mutate(n.id);
    if (n.link) {
      setOpen(false);
      const path = toAppPath(n.link);
      if (path.startsWith("/")) router.push(path);
      else window.open(path, "_blank", "noopener");
    }
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button variant="ghost" size="icon" className="relative rounded-xl" aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`} />
        }
      >
        <Bell className="size-[18px]" />
        {unread > 0 && (
          <span className="absolute -top-0.5 -right-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-primary px-1 text-[10px] font-bold text-white tabular-nums ring-2 ring-background">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(92vw,380px)] gap-0 p-0">
        <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-3">
          <div>
            <p className="font-heading text-sm font-semibold">Notifications</p>
            <p className="text-xs text-muted-foreground">{unread ? `${unread} unread` : "You're all caught up"}</p>
          </div>
          <Button variant="ghost" size="sm" disabled={!unread || mark.isPending} onClick={() => mark.mutate("all")}>
            <CheckCheck /> Mark all read
          </Button>
        </div>
        <div className="max-h-[420px] overflow-y-auto p-1.5">
          {isLoading ? (
            <div className="space-y-2 p-2">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-12" />
              ))}
            </div>
          ) : !data?.items.length ? (
            <div className="flex flex-col items-center gap-2 py-10 text-center text-sm text-muted-foreground">
              <BellOff className="size-6 opacity-60" />
              No notifications yet. Processing, rendering and publishing updates appear here.
            </div>
          ) : (
            <ul>
              {data.items.map((n) => {
                const meta = ICONS[n.type] ?? { icon: Info, className: "text-muted-foreground" };
                return (
                  <li key={n.id}>
                    <button
                      type="button"
                      onClick={() => openItem(n)}
                      className={cn(
                        "flex w-full gap-3 rounded-lg px-2.5 py-2.5 text-left transition-colors hover:bg-white/[0.04] focus-visible:bg-white/[0.06] focus-visible:outline-none",
                        !n.read_at && "bg-white/[0.025]",
                      )}
                    >
                      <meta.icon className={cn("mt-0.5 size-4 shrink-0", meta.className)} />
                      <span className="min-w-0 flex-1">
                        <span className="flex items-start justify-between gap-2">
                          <span className={cn("text-sm", !n.read_at && "font-medium")}>{n.title}</span>
                          {!n.read_at && <span className="mt-1.5 size-2 shrink-0 rounded-full bg-ig-pink" aria-label="Unread" />}
                        </span>
                        {n.body && <span className="mt-0.5 line-clamp-2 block text-xs text-muted-foreground">{n.body}</span>}
                        <span className="mt-1 block text-[11px] text-muted-foreground/70">{formatRelative(n.created_at)}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
