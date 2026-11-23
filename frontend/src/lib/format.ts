import { format, formatDistanceToNowStrict } from "date-fns";
import { TZDate } from "@date-fns/tz";

/** 75.4 -> "1:15" ; 3725 -> "1:02:05" */
export function formatTimestamp(seconds: number | null | undefined, withTenths = false): string {
  if (seconds == null || !Number.isFinite(seconds)) return "–";
  const neg = seconds < 0;
  const s = Math.abs(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const secStr = withTenths ? sec.toFixed(1).padStart(4, "0") : String(Math.floor(sec)).padStart(2, "0");
  const body = h > 0 ? `${h}:${String(m).padStart(2, "0")}:${secStr}` : `${m}:${secStr}`;
  return neg ? `-${body}` : body;
}

/** 75.4 -> "00:01:15" (always hh:mm:ss, for original-video timestamps). */
export function formatHms(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "–";
  const s = Math.max(0, Math.floor(seconds));
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
}

/** Sequence number as "Clip 01". */
export const clipNumber = (sequence: number | null | undefined) => (sequence ? `Clip ${String(sequence).padStart(2, "0")}` : "Clip");

/** 75 -> "1m 15s", 42.3 -> "42s" */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "–";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h}h ${m}m`;
  if (m) return sec ? `${m}m ${sec}s` : `${m}m`;
  return `${sec}s`;
}

export function formatBytes(bytes: number | null | undefined, digits = 1): string {
  if (bytes == null || !Number.isFinite(bytes)) return "–";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : digits)} ${units[i]}`;
}

export function formatSpeed(bytesPerSec: number): string {
  return `${formatBytes(bytesPerSec)}/s`;
}

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const plain = new Intl.NumberFormat("en");
export function formatCount(n: number | null | undefined, compactForm = true): string {
  if (n == null || !Number.isFinite(n)) return "–";
  return compactForm && Math.abs(n) >= 10000 ? compact.format(n) : plain.format(n);
}

export function formatPercent(ratio: number | null | undefined, digits = 0): string {
  if (ratio == null || !Number.isFinite(ratio)) return "–";
  return `${(ratio * 100).toFixed(digits)}%`;
}

function toDate(value: string | Date, tz?: string | null): Date {
  const d = typeof value === "string" ? new Date(value) : value;
  return tz ? new TZDate(d.getTime(), tz) : d;
}

/** Format an ISO timestamp in the given IANA timezone (falls back to the browser zone). */
export function formatInTz(value: string | Date | null | undefined, pattern: string, tz?: string | null): string {
  if (!value) return "–";
  try {
    return format(toDate(value, tz), pattern);
  } catch {
    return format(toDate(value), pattern);
  }
}

export const formatDateTime = (v: string | Date | null | undefined, tz?: string | null) =>
  formatInTz(v, "MMM d, yyyy · HH:mm", tz);
export const formatDate = (v: string | Date | null | undefined, tz?: string | null) => formatInTz(v, "MMM d, yyyy", tz);
export const formatTime = (v: string | Date | null | undefined, tz?: string | null) => formatInTz(v, "HH:mm", tz);

export function formatRelative(value: string | Date | null | undefined): string {
  if (!value) return "–";
  const d = typeof value === "string" ? new Date(value) : value;
  return formatDistanceToNowStrict(d, { addSuffix: true });
}

/** Countdown text "2d 4h", "3h 12m", "4m 05s", or "now". */
export function formatCountdown(ms: number): string {
  if (ms <= 0) return "now";
  const s = Math.floor(ms / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(sec).padStart(2, "0")}s`;
}

export function titleCase(s: string | null | undefined): string {
  if (!s) return "";
  return s.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export const JOB_TYPE_LABELS: Record<string, string> = {
  analysis: "AI video analysis",
  clip_vision: "Face tracking & framing",
  transcribe_clip: "Clip re-transcription",
  render: "Render 9:16 export",
  publish: "Publish to platform",
  import: "Cloud import",
  metadata: "Titles, descriptions & hashtags",
  analytics: "Platform statistics refresh",
};

export const jobTypeLabel = (t: string) => JOB_TYPE_LABELS[t] ?? titleCase(t);

/** Friendly names for processing stages (v2 pipeline keys and the older analysis job stages). */
export const STAGE_LABELS: Record<string, string> = {
  ingestion: "Preparing video",
  audio: "Extracting audio",
  transcription: "Transcribing speech",
  video_analysis: "Analysing video",
  highlights: "Finding highlights",
  clip_generation: "Generating clips",
  metadata: "Writing titles & hashtags",
  completed: "Completed",
  probe: "Reading video",
  scenes: "Detecting scenes",
  visual: "Analysing visuals",
  done: "Done",
};
export const stageLabel = (key: string | null | undefined) => (key ? STAGE_LABELS[key] ?? titleCase(key) : "");

export const PROCESSING_MODES = [
  { value: "fast", label: "Fast", hint: "Quickest results — lighter transcription and analysis models." },
  { value: "balanced", label: "Balanced", hint: "Recommended — good accuracy at a reasonable speed." },
  { value: "best", label: "Best", hint: "Most accurate transcription and highlight detection, but slowest." },
] as const;

export const CATEGORY_LABELS: Record<string, string> = {
  funny: "Funny moment",
  exciting: "Exciting",
  strong_statement: "Strong statement",
  emotional: "Emotional",
  explanation: "Explanation",
  surprising: "Surprising",
  gaming_achievement: "Gaming achievement",
  reaction: "Reaction",
  audience_interaction: "Audience interaction",
  conversation: "Conversation",
  other: "Highlight",
};
export const categoryLabel = (c: string | null | undefined) => (c ? CATEGORY_LABELS[c] ?? titleCase(c) : "Highlight");

export const AI_SCORE_LABEL = "AI highlight score (estimate)";

/** Browser timezone, guarded for SSR. */
export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function allTimezones(): string[] {
  try {
    const list = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.("timeZone");
    if (list?.length) return list.includes("UTC") ? list : ["UTC", ...list];
  } catch {
    /* fall through */
  }
  return ["UTC", "Europe/London", "Europe/Berlin", "America/New_York", "America/Chicago", "America/Los_Angeles", "Asia/Kolkata", "Asia/Tokyo", "Australia/Sydney"];
}
