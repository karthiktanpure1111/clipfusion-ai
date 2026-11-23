import { cn } from "@/lib/utils";
import { titleCase } from "@/lib/format";

type Tone = "neutral" | "info" | "progress" | "success" | "warning" | "danger" | "brand";

const TONE: Record<Tone, string> = {
  neutral: "bg-white/5 text-muted-foreground ring-white/10",
  info: "bg-info/10 text-info ring-info/25",
  progress: "bg-ig-purple/15 text-[#c79cf0] ring-ig-purple/30",
  success: "bg-success/10 text-success ring-success/25",
  warning: "bg-warning/10 text-warning ring-warning/25",
  danger: "bg-destructive/10 text-[#ff5c7a] ring-destructive/30",
  brand: "bg-ig-pink/10 text-[#f07ab4] ring-ig-pink/30",
};

const STATUS_TONE: Record<string, Tone> = {
  // sources
  pending_upload: "neutral",
  metadata_only: "info",
  importing: "progress",
  uploaded: "info",
  queued: "neutral",
  processing: "progress",
  ready: "success",
  // jobs
  running: "progress",
  retrying: "warning",
  succeeded: "success",
  cancelled: "neutral",
  failed: "danger",
  // clips
  pending: "neutral",
  generated: "info",
  uploading: "progress",
  upload_failed: "danger",
  removed: "neutral",
  cleaned: "success",
  not_rendered: "neutral",
  rendering: "progress",
  rendered: "success",
  stale: "warning",
  // queue
  pending_approval: "warning",
  scheduled: "info",
  publishing: "progress",
  processing_remote: "progress",
  published: "success",
  // accounts
  active: "success",
  expired: "warning",
  revoked: "danger",
  error: "danger",
  paused: "warning",
};

const LABELS: Record<string, string> = {
  pending_upload: "Awaiting file",
  metadata_only: "Metadata only",
  not_rendered: "Not rendered",
  pending: "Pending",
  upload_failed: "Upload failed",
  pending_approval: "Awaiting approval",
  processing_remote: "Platform processing",
  stale: "Render outdated",
  succeeded: "Done",
};

const PULSE = new Set(["importing", "processing", "running", "rendering", "publishing", "processing_remote", "retrying", "uploading"]);

export function StatusBadge({
  status,
  label,
  tone,
  className,
}: {
  status: string;
  label?: string;
  tone?: Tone;
  className?: string;
}) {
  const t = tone ?? STATUS_TONE[status] ?? "neutral";
  return (
    <span
      className={cn(
        "inline-flex h-5 shrink-0 items-center gap-1.5 rounded-full px-2 text-[11px] font-medium ring-1 ring-inset whitespace-nowrap",
        TONE[t],
        className,
      )}
    >
      <span className={cn("size-1.5 rounded-full bg-current", PULSE.has(status) && "animate-pulse")} aria-hidden />
      {label ?? LABELS[status] ?? titleCase(status)}
    </span>
  );
}

const CLIP_LABELS: Record<string, string> = {
  pending: "Queued",
  processing: "Processing",
  generated: "Generated",
  uploading: "Uploading",
  uploaded: "Uploaded",
  upload_failed: "Upload failed",
  removed: "Removed",
  cleaned: "Cleaned",
};

/** v2 clip status; falls back to the render state for an older backend that has no `status`. */
export function clipStatusOf(c: { status?: string; render_status: string; output_url: string | null }): string {
  if (c.status) return c.status;
  return c.render_status === "rendered" || c.output_url ? "generated" : "processing";
}

export function ClipStatusBadge({ clip, className }: { clip: { status?: string; render_status: string; output_url: string | null }; className?: string }) {
  const s = clipStatusOf(clip);
  return <StatusBadge status={s} label={CLIP_LABELS[s]} tone={s === "uploaded" ? "success" : s === "cleaned" ? "neutral" : undefined} className={className} />;
}
