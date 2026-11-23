"use client";

import { useState } from "react";
import Link from "next/link";
import { HardDrive, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { ErrorState, SectionCard } from "@/components/common/page";
import { useDeleteOriginal, useStorage } from "@/lib/api/hooks/sources";
import type { StorageOut, StorageSource } from "@/lib/api/types";
import { formatBytes, formatCount } from "@/lib/format";
import { cn } from "@/lib/utils";

const PARTS: { key: keyof StorageOut; label: string; color: string }[] = [
  { key: "originals_bytes", label: "Originals", color: "#8f96ff" },
  { key: "renders_bytes", label: "Rendered clips", color: "#D058C8" },
  { key: "proxies_bytes", label: "Preview proxies", color: "#FCAF45" },
  { key: "thumbnails_bytes", label: "Thumbnails", color: "#38BDF8" },
  { key: "temp_bytes", label: "Temporary files", color: "#6b6b78" },
];

function Breakdown({ data }: { data: StorageOut }) {
  const total = Math.max(1, PARTS.reduce((s, p) => s + ((data[p.key] as number) || 0), 0));
  return (
    <div className="space-y-2.5">
      <div className="flex h-2.5 overflow-hidden rounded-full bg-white/8" role="img" aria-label="Managed storage by type">
        {PARTS.map((p) => {
          const v = (data[p.key] as number) || 0;
          return v > 0 ? <span key={p.key} style={{ width: `${(v / total) * 100}%`, background: p.color }} className="h-full first:rounded-l-full last:rounded-r-full" /> : null;
        })}
      </div>
      <ul className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-3">
        {PARTS.map((p) => (
          <li key={p.key} className="flex items-center gap-1.5">
            <span className="size-2 shrink-0 rounded-full" style={{ background: p.color }} aria-hidden />
            <span className="text-muted-foreground">{p.label}</span>
            <span className="ml-auto tabular-nums">{formatBytes(data[p.key] as number)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Stat({ label, value, className }: { label: string; value: React.ReactNode; className?: string }) {
  return (
    <div className="rounded-xl border border-white/7 bg-black/15 px-3 py-2">
      <p className="text-[10px] tracking-wide text-muted-foreground uppercase">{label}</p>
      <p className={cn("font-heading text-lg font-bold tabular-nums", className)}>{value}</p>
    </div>
  );
}

function SourceRow({ s }: { s: StorageSource }) {
  const del = useDeleteOriginal();
  const [confirm, setConfirm] = useState(false);
  return (
    <li className="flex items-center gap-3 rounded-xl border border-white/6 bg-black/10 px-3 py-2">
      <div className="min-w-0 flex-1">
        <Link href={`/app/videos/${s.id}`} className="block truncate text-sm font-medium hover:underline">
          {s.title || "Untitled video"}
        </Link>
        <p className="text-xs text-muted-foreground">
          Original {s.original_available ? formatBytes(s.original_bytes) : "deleted"} · clips {formatBytes(s.clip_bytes)} · {s.clips_done}/{s.clips_total} clips done
        </p>
      </div>
      {s.original_available && s.all_done && (
        <Button size="sm" variant="outline" className="shrink-0 rounded-lg" onClick={() => setConfirm(true)}>
          <Trash2 /> Delete original
        </Button>
      )}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Delete the original video?"
        description={`Deletes only the original long video file and its preview proxies (${formatBytes(s.original_bytes)}) from this computer. Clip records, transcripts and YouTube links stay. You won't be able to re-process this video or re-render its clips without adding the file again.`}
        confirmLabel="Delete original"
        destructive
        pending={del.isPending}
        onConfirm={() =>
          del.mutate(s.id, {
            onSuccess: (r) => {
              const freed = r?.freed_bytes ?? s.original_bytes;
              toast.success("Original video deleted", freed ? { description: `Freed ${formatBytes(freed)}` } : undefined);
              setConfirm(false);
            },
          })
        }
      />
    </li>
  );
}

/** Managed storage (GET /storage): usage by type, clip counts, freed space, free disk, per-video originals. */
export function StoragePanel({ compact = false }: { compact?: boolean }) {
  const { data, isLoading, error, refetch } = useStorage();
  const sources = data ? [...data.sources].sort((a, b) => (b.original_bytes ?? 0) + (b.clip_bytes ?? 0) - ((a.original_bytes ?? 0) + (a.clip_bytes ?? 0))) : [];
  const shown = compact ? sources.slice(0, 4) : sources;
  return (
    <SectionCard
      title="Storage"
      description="Space used by ClipFusion on this computer"
      icon={HardDrive}
      actions={
        compact ? (
          <Link href="/app/settings#storage" className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "rounded-lg")}>
            Manage
          </Link>
        ) : undefined
      }
    >
      {error ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !data ? (
        <Skeleton className="h-40 rounded-xl" />
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
            <Stat label="Managed storage" value={formatBytes(data.managed_bytes)} />
            <Stat label="Total freed" value={formatBytes(data.freed_bytes_total)} className="text-success" />
            <Stat label="Free disk" value={formatBytes(data.disk_free_bytes)} />
            <Stat label="Clips" value={formatCount(data.clips.total)} />
          </div>
          <Breakdown data={data} />
          <p className="text-xs text-muted-foreground">
            {formatCount(data.clips.generated)} generated · {formatCount(data.clips.uploaded)} uploaded · {formatCount(data.clips.cleaned)} cleaned (files deleted after upload) ·{" "}
            {formatCount(data.clips.removed)} removed
          </p>
          {shown.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-xs font-medium">By video</p>
              <ul className="space-y-1.5">
                {shown.map((s) => (
                  <SourceRow key={s.id} s={s} />
                ))}
              </ul>
              {compact && sources.length > shown.length && <p className="text-xs text-muted-foreground">+ {sources.length - shown.length} more in Settings → Storage</p>}
              <p className="text-[11px] text-muted-foreground">&ldquo;Delete original&rdquo; appears once every clip of a video is uploaded or removed.</p>
            </div>
          )}
        </div>
      )}
    </SectionCard>
  );
}
