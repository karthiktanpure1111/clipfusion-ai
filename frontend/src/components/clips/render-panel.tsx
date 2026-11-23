"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, Crop, Download, Film, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { SectionCard } from "@/components/common/page";
import { StatusBadge } from "@/components/common/status-badge";
import { JobProgressBar } from "@/components/processing/job-card";
import { ApiError } from "@/lib/api/client";
import { getDownloadUrl, useRenderClip } from "@/lib/api/hooks/clips";
import { toastError } from "@/lib/api/hooks/common";
import type { ClipDetail, FramingWarning } from "@/lib/api/types";
import { formatDuration, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

export function RenderPanel({ clip }: { clip: ClipDetail }) {
  const render = useRenderClip(clip.id);
  const [warnings, setWarnings] = useState<FramingWarning[] | null>(null);
  const [downloading, setDownloading] = useState(false);
  const job = clip.active_jobs.find((j) => j.job_type === "render");
  const busy = clip.render_status === "queued" || clip.render_status === "rendering" || !!job;

  const start = async (ack: boolean) => {
    try {
      await render.mutateAsync(ack);
      setWarnings(null);
      toast.success("Render queued");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.code === "framing_warnings_unacknowledged") {
        setWarnings(Array.isArray(e.details) ? (e.details as FramingWarning[]) : []);
      } else toastError(e);
    }
  };

  const download = async () => {
    setDownloading(true);
    try {
      const { url, filename } = await getDownloadUrl(clip.id);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (e) {
      toastError(e);
    } finally {
      setDownloading(false);
    }
  };

  return (
    <SectionCard title="Render 9:16 export" description="Clean FFmpeg export (no burned-in text) with your framing and render options." icon={Film}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <StatusBadge status={clip.render_status} />
        {clip.rendered_at && (
          <span className="text-xs text-muted-foreground">
            {formatRelative(clip.rendered_at)}
            {clip.output_duration ? ` · ${formatDuration(clip.output_duration)}` : ""}
          </span>
        )}
      </div>
      {job && (
        <div className="mt-3 space-y-1">
          <JobProgressBar value={job.progress} status={job.status} />
          <p className="text-xs text-muted-foreground">{job.message ?? "Rendering…"} · {Math.round(job.progress * 100)}%</p>
        </div>
      )}
      {clip.render_error && clip.render_status === "failed" && <p className="mt-3 rounded-lg bg-destructive/8 p-2.5 text-xs text-[#ff8098]">{clip.render_error}</p>}
      {clip.blocking_warning_count > 0 && !clip.warnings_acknowledged && !warnings && (
        <p className="mt-3 flex gap-2 text-xs text-warning">
          <AlertTriangle className="size-3.5 shrink-0" /> {clip.blocking_warning_count} framing warning{clip.blocking_warning_count === 1 ? "" : "s"} — a face or important area may be cut off.
        </p>
      )}

      {warnings && (
        <div role="alert" className="mt-4 space-y-3 rounded-xl border border-warning/30 bg-warning/5 p-3.5">
          <p className="flex items-center gap-2 text-sm font-medium text-warning">
            <AlertTriangle className="size-4" /> Review the framing before rendering
          </p>
          <ul className="space-y-1.5 text-sm">
            {warnings.map((w, i) => (
              <li key={i} className="flex gap-2">
                <span className={cn("mt-1.5 size-1.5 shrink-0 rounded-full", w.severity === "critical" ? "bg-destructive" : "bg-warning")} />
                <span>
                  {w.message}
                  {w.affected_fraction != null && <span className="text-muted-foreground"> · affects {Math.round(w.affected_fraction * 100)}% of the clip</span>}
                </span>
              </li>
            ))}
            {!warnings.length && <li className="text-muted-foreground">The framing has unacknowledged warnings.</li>}
          </ul>
          <div className="flex flex-wrap gap-2">
            <Link href={`/app/clips/${clip.id}/framing`} className={cn(buttonVariants({ size: "sm" }))}>
              <Crop /> Fix framing
            </Link>
            <Button size="sm" variant="outline" onClick={() => start(true)} disabled={render.isPending}>
              {render.isPending && <Loader2 className="animate-spin" />} Render anyway (I reviewed the framing)
            </Button>
          </div>
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        <Button className="glow-red" onClick={() => start(false)} disabled={busy || render.isPending}>
          {busy || render.isPending ? <Loader2 className="animate-spin" /> : <Film />}
          {clip.render_status === "rendered" || clip.render_status === "stale" ? "Re-render" : "Render"}
        </Button>
        <Button variant="outline" onClick={download} disabled={!clip.output_url || downloading}>
          {downloading ? <Loader2 className="animate-spin" /> : <Download />} Download MP4
        </Button>
      </div>
    </SectionCard>
  );
}
