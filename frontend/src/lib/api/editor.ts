"use client";

/**
 * Typed API + React Query hooks for the Framing editor
 * (docs/API.md: Clips, Framing, Assets, Rendering, Jobs).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError, buildUrl, request } from "@/lib/api/client";
import type { ClipVisionAnalysis, CompiledLayout, FramingConfig, FramingMode, FramingWarning } from "@/lib/framing/types";

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type JobStatus = "queued" | "running" | "retrying" | "succeeded" | "failed" | "cancelled";
export interface JobOut {
  id: string;
  job_type: string;
  status: JobStatus;
  stage: string | null;
  progress: number;
  message: string | null;
  error: string | null;
  clip_id: string | null;
  result: unknown;
  created_at: string;
  finished_at: string | null;
}
export const isJobActive = (j?: JobOut | null) => !!j && ["queued", "running", "retrying"].includes(j.status);

export interface ClipOut {
  id: string;
  video_source_id: string;
  source_title: string | null;
  start_time: number;
  end_time: number;
  duration: number;
  title: string | null;
  highlight_score: number | null;
  status?: string;
  sequence?: number | null;
  render_status: "not_rendered" | "queued" | "rendering" | "rendered" | "failed" | "stale";
  render_is_current: boolean;
  thumbnail_url: string | null;
  proxy_url: string | null;
  proxy_start: number;
  framing_mode: FramingMode | null;
  warning_count: number;
  blocking_warning_count: number;
  warnings_acknowledged: boolean;
  created_at: string;
  updated_at: string;
}

export interface ClipDetail extends ClipOut {
  render_error: string | null;
  active_jobs: JobOut[];
  source: { id: string; title: string; duration: number; width: number; height: number; fps: number };
}

export interface FramingOut {
  /** With apply_to_video: how many other clips of the video got this framing. */
  applied_to_clips?: number | null;
  clip_id: string;
  mode: FramingMode;
  config: FramingConfig;
  vision: ClipVisionAnalysis | null;
  warnings: FramingWarning[];
  warnings_acknowledged: boolean;
  auto_generated: boolean;
  compiled: CompiledLayout;
  source: { width: number; height: number; fps: number; duration: number };
  clip_start: number;
  clip_end: number;
  clip_duration: number;
  proxy_url: string | null;
  proxy_start: number;
}

export interface FramingCheckOut {
  config: FramingConfig;
  warnings: FramingWarning[];
  compiled: CompiledLayout;
}

export interface FramingPresetOut {
  id: string;
  name: string;
  mode: FramingMode;
  config: FramingConfig;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

export interface AssetOut {
  id: string;
  kind: "intro" | "outro" | "watermark" | "background";
  name: string;
  url: string | null;
  width: number | null;
  height: number | null;
}

/** Mutation errors are shown by the editors themselves (not by the global toast). */
const SILENT = { silentError: true };

/** Detail/preset keys match src/lib/api/hooks/common.ts (same endpoint, same shape). */
export const editorKeys = {
  clip: (id: string) => ["clip", id] as const,
  clips: (q: Record<string, unknown>) => ["clips", "list", q] as const,
  framing: (id: string) => ["framing", id] as const,
  framingPresets: ["framing-presets"] as const,
  assets: (kind: string) => ["assets", kind] as const,
  job: (id: string) => ["jobs", id] as const,
};

// ------------------------------------------------------------------ clips & jobs
export const useClip = (id: string) =>
  useQuery({ queryKey: editorKeys.clip(id), queryFn: ({ signal }) => api.get<ClipDetail>(`/clips/${id}`, undefined, signal) });

export const useClips = (q: { limit?: number; offset?: number; sort?: string; q?: string } = {}) =>
  useQuery({
    queryKey: editorKeys.clips(q),
    queryFn: ({ signal }) => api.get<Page<ClipOut>>("/clips", { limit: 50, sort: "created", ...q }, signal),
  });

/** Polls GET /jobs/{id} every 1.5 s while the job is active. */
export const useJob = (id: string | null) =>
  useQuery({
    queryKey: editorKeys.job(id ?? "none"),
    queryFn: ({ signal }) => api.get<JobOut>(`/jobs/${id}`, undefined, signal),
    enabled: !!id,
    refetchInterval: (query) => (isJobActive(query.state.data) || !query.state.data ? 1500 : false),
  });

// ------------------------------------------------------------------ framing
export const useFraming = (id: string) =>
  useQuery({ queryKey: editorKeys.framing(id), queryFn: ({ signal }) => api.get<FramingOut>(`/clips/${id}/framing`, undefined, signal) });

export function useSaveFraming(id: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: SILENT,
    mutationFn: ({ config, applyToVideo }: { config: FramingConfig; applyToVideo: boolean }) =>
      api.put<FramingOut>(`/clips/${id}/framing${applyToVideo ? "?apply_to_video=true" : ""}`, { config }),
    onSuccess: (data) => {
      qc.setQueryData(editorKeys.framing(id), data);
      void qc.invalidateQueries({ queryKey: ["clips"] });
      void qc.invalidateQueries({ queryKey: editorKeys.clip(id) });
    },
  });
}

/** Dry-run crop protection (abortable; used live by the editor). */
export const checkFraming = (id: string, config: FramingConfig, signal?: AbortSignal) =>
  request<FramingCheckOut>(`/clips/${id}/framing/check`, { method: "POST", body: { config }, signal });

export const useSuggestFraming = (id: string) => useMutation({ meta: SILENT, mutationFn: () => api.post<FramingCheckOut>(`/clips/${id}/framing/suggest`) });

export const useAnalyzeFraming = (id: string) => useMutation({ meta: SILENT, mutationFn: () => api.post<JobOut>(`/clips/${id}/framing/analyze`) });

export function useAcknowledgeFraming(id: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: SILENT,
    mutationFn: () => api.post<FramingOut>(`/clips/${id}/framing/acknowledge`),
    onSuccess: (data) => qc.setQueryData(editorKeys.framing(id), data),
  });
}

export interface ApplyFramingResult {
  results: { clip_id: string; ok: boolean; warnings: FramingWarning[]; error: { code: string; message: string } | string | null }[];
}
export function useApplyFraming() {
  const qc = useQueryClient();
  return useMutation({
    meta: SILENT,
    mutationFn: (body: { clip_ids: string[]; config?: FramingConfig; preset_id?: string }) => api.post<ApplyFramingResult>("/framing/apply", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["clips"] });
      void qc.invalidateQueries({ queryKey: ["framing"] });
    },
  });
}

export const useFramingPresets = () =>
  useQuery({ queryKey: editorKeys.framingPresets, queryFn: ({ signal }) => api.get<FramingPresetOut[]>("/framing-presets", undefined, signal) });

function usePresetMutation<TVars, TOut>(key: readonly string[], fn: (v: TVars) => Promise<TOut>) {
  const qc = useQueryClient();
  return useMutation({ meta: SILENT, mutationFn: fn, onSuccess: () => qc.invalidateQueries({ queryKey: key }) });
}

export const useCreateFramingPreset = () =>
  usePresetMutation(editorKeys.framingPresets, (b: { name: string; config: FramingConfig; is_default?: boolean }) =>
    api.post<FramingPresetOut>("/framing-presets", b),
  );
export const useUpdateFramingPreset = () =>
  usePresetMutation(editorKeys.framingPresets, ({ id, ...b }: { id: string; name?: string; is_default?: boolean }) =>
    api.patch<FramingPresetOut>(`/framing-presets/${id}`, b),
  );
export const useDeleteFramingPreset = () => usePresetMutation(editorKeys.framingPresets, (id: string) => api.delete(`/framing-presets/${id}`));

// ------------------------------------------------------------------ assets
export const useAssets = (kind: AssetOut["kind"]) =>
  useQuery({ queryKey: editorKeys.assets(kind), queryFn: ({ signal }) => api.get<AssetOut[]>("/assets", { kind }, signal) });

// ------------------------------------------------------------------ rendering
export function useRenderClip(id: string) {
  const qc = useQueryClient();
  return useMutation({
    meta: SILENT,
    mutationFn: (acknowledge: boolean) => api.post<JobOut>(`/clips/${id}/render`, { acknowledge_warnings: acknowledge }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["clips"] }),
  });
}

/** One frame rendered by the real export renderer (``image/jpeg``). */
export async function fetchPreviewFrame(id: string, body: { t: number; framing?: FramingConfig }, signal?: AbortSignal): Promise<Blob> {
  const res = await fetch(buildUrl(`/clips/${id}/preview-frame`), {
    method: "POST",
    credentials: "include",
    headers: { "X-Requested-With": "ClipFusion", "Content-Type": "application/json", Accept: "image/jpeg" },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok) {
    let code = "http_error";
    let message = `Preview failed (${res.status})`;
    try {
      const data = await res.json();
      code = data?.error?.code ?? code;
      message = data?.error?.message ?? message;
    } catch {
      /* non-JSON */
    }
    throw new ApiError(res.status, code, message);
  }
  return res.blob();
}

export const errorMessage = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong");
