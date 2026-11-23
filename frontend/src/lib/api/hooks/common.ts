"use client";

import { toast } from "sonner";
import { ApiError } from "@/lib/api/client";

/** Show an API error as a toast (uses ApiError.message from the backend). */
export function toastError(err: unknown, fallback = "Something went wrong") {
  if (err instanceof ApiError) {
    if (err.code === "aborted") return;
    toast.error(err.message || fallback, err.code && err.code !== "http_error" ? { description: humanCode(err.code) } : undefined);
  } else if (err instanceof Error) {
    toast.error(err.message || fallback);
  } else {
    toast.error(fallback);
  }
}

function humanCode(code: string): string | undefined {
  switch (code) {
    case "integration_not_configured":
      return "This integration is not configured on the server.";
    case "quota_exceeded":
      return "Platform quota exceeded — try again later.";
    case "rate_limited":
      return "Too many requests — slow down a little.";
    case "dependency_missing":
      return "A required server dependency is missing.";
    case "upload_in_progress":
      return "An upload run is already in progress for this YouTube account. Wait for it to finish or cancel it first.";
    case "clip_uploading":
      return "This clip is uploading right now. Try again once the upload has finished.";
    default:
      return undefined;
  }
}

export const isApiError = (e: unknown): e is ApiError => e instanceof ApiError;
export const isNotConfigured = (e: unknown) => e instanceof ApiError && (e.status === 503 || e.code === "integration_not_configured");

/** Centralised query keys. */
export const qk = {
  me: ["me"] as const,
  dashboard: ["dashboard"] as const,
  capabilities: ["capabilities"] as const,
  notifications: ["notifications"] as const,
  sources: (params?: object) => ["sources", params ?? {}] as const,
  source: (id: string) => ["source", id] as const,
  pipeline: (id: string) => ["source", id, "pipeline"] as const,
  uploadRuns: (sourceId: string) => ["upload-runs", sourceId] as const,
  storage: ["storage"] as const,
  transcript: (id: string) => ["source", id, "transcript"] as const,
  mediaUrl: (id: string) => ["source", id, "media-url"] as const,
  jobs: (params?: object) => ["jobs", params ?? {}] as const,
  clips: (params?: object) => ["clips", params ?? {}] as const,
  clip: (id: string) => ["clip", id] as const,
  clipRender: (id: string) => ["clip", id, "render"] as const,
  accounts: ["accounts"] as const,
  requirements: ["accounts", "requirements"] as const,
  queue: (params?: object) => ["queue", params ?? {}] as const,
  calendar: (start: string, end: string) => ["calendar", start, end] as const,
  next: ["publishing-next"] as const,
  schedules: ["schedules"] as const,
  schedulePreview: (id: string) => ["schedules", id, "preview"] as const,
  analytics: (days: number) => ["analytics", days] as const,
  framingPresets: ["framing-presets"] as const,
  assets: ["assets"] as const,
};

export const ACTIVE_JOB_STATUSES = new Set(["queued", "running", "retrying"]);
export const isJobActive = (status: string) => ACTIVE_JOB_STATUSES.has(status);
