"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  BulkAction,
  BulkResult,
  ClipDetail,
  ClipMetadata,
  ClipOut,
  JobOut,
  Page,
  RemoveClipOut,
  RenderStatusOut,
} from "@/lib/api/types";
import { qk } from "./common";

export interface ClipListParams {
  video_source_id?: string;
  /** v2 clip status (processing, generated, uploading, uploaded, upload_failed, cleaned…). */
  status?: string;
  render_status?: string;
  min_score?: number;
  q?: string;
  sort?: "sequence" | "score" | "created";
  include_removed?: boolean;
  limit?: number;
  offset?: number;
}

const RENDER_BUSY = new Set(["queued", "rendering"]);
const CLIP_BUSY = new Set(["pending", "processing", "uploading"]);
/** A clip whose state is still changing on the server (render, processing or upload). */
export const isClipBusy = (c: ClipOut) => RENDER_BUSY.has(c.render_status) || (!!c.status && CLIP_BUSY.has(c.status));

export function useClips(params: ClipListParams = {}, opts: { pollMs?: number | false } = {}) {
  return useQuery({
    queryKey: qk.clips(params),
    queryFn: ({ signal }) => api.get<Page<ClipOut>>("/clips", { limit: 60, ...params }, signal),
    placeholderData: (prev) => prev,
    refetchInterval: (q) => opts.pollMs || (q.state.data?.items.some(isClipBusy) ? 4000 : false),
  });
}

const CLIP_PAGE = 48;

/** Offset-paged clips for the library (the API caps `limit` at 200, so pages are fetched separately). */
export function useClipPages(params: Omit<ClipListParams, "limit" | "offset">) {
  return useInfiniteQuery({
    queryKey: qk.clips({ ...params, paged: true }),
    queryFn: ({ pageParam, signal }) => api.get<Page<ClipOut>>("/clips", { ...params, limit: CLIP_PAGE, offset: pageParam }, signal),
    initialPageParam: 0,
    getNextPageParam: (last) => (last.offset + last.items.length < last.total && last.items.length > 0 ? last.offset + last.items.length : undefined),
    placeholderData: (prev) => prev,
    refetchInterval: (q) => (q.state.data?.pages.some((p) => p.items.some(isClipBusy)) ? 4000 : false),
  });
}

export function useClip(id: string | undefined) {
  return useQuery({
    queryKey: qk.clip(id ?? ""),
    queryFn: ({ signal }) => api.get<ClipDetail>(`/clips/${id}`, undefined, signal),
    enabled: !!id,
    refetchInterval: (q) => {
      const d = q.state.data;
      if (!d) return false;
      return d.active_jobs.length || isClipBusy(d) ? 2500 : false;
    },
  });
}

export function useClipRender(id: string, enabled: boolean) {
  return useQuery({
    queryKey: qk.clipRender(id),
    queryFn: ({ signal }) => api.get<RenderStatusOut>(`/clips/${id}/render`, undefined, signal),
    enabled,
    refetchInterval: (q) => (q.state.data && RENDER_BUSY.has(q.state.data.render_status) ? 2000 : false),
  });
}

function useClipInvalidate() {
  const qc = useQueryClient();
  return (detail?: ClipDetail, id?: string) => {
    if (detail) qc.setQueryData(qk.clip(detail.id), detail);
    if (id) qc.invalidateQueries({ queryKey: qk.clip(id) });
    qc.invalidateQueries({ queryKey: ["clips"] });
    qc.invalidateQueries({ queryKey: qk.dashboard });
  };
}

export interface ClipPatch {
  start_time?: number;
  end_time?: number;
  title?: string;
  social_metadata?: Partial<ClipMetadata> | { youtube?: Partial<ClipMetadata["youtube"]>; instagram?: Partial<ClipMetadata["instagram"]> };
  render_options?: Record<string, unknown>;
}

export function useUpdateClip(id: string) {
  const done = useClipInvalidate();
  return useMutation({
    mutationFn: (body: ClipPatch) => api.patch<ClipDetail>(`/clips/${id}`, body),
    onSuccess: (d) => done(d),
  });
}

/** Remove Clip (DELETE /clips/{id}): deletes this clip's MP4, proxy, thumbnail and temp files; a small record stays. */
export function useRemoveClip() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete<RemoveClipOut | undefined>(`/clips/${id}`),
    onSuccess: (_r, id) => {
      qc.removeQueries({ queryKey: qk.clip(id) });
      qc.invalidateQueries({ queryKey: ["clips"] });
      qc.invalidateQueries({ queryKey: ["source"] });
      qc.invalidateQueries({ queryKey: qk.storage });
      qc.invalidateQueries({ queryKey: qk.dashboard });
    },
  });
}

export function useBulkClips() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { ids: string[]; action: BulkAction; acknowledge_warnings?: boolean }) =>
      api.post<BulkResult>("/clips/bulk", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.storage });
      qc.invalidateQueries({ queryKey: ["clips"] });
      qc.invalidateQueries({ queryKey: ["clip"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: qk.dashboard });
    },
  });
}

export function useRenderClip(id: string) {
  const done = useClipInvalidate();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (acknowledge_warnings: boolean) => api.post<JobOut>(`/clips/${id}/render`, { acknowledge_warnings }),
    onSuccess: () => {
      done(undefined, id);
      qc.invalidateQueries({ queryKey: qk.clipRender(id) });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    meta: { silentError: true },
  });
}

export function useRegenerateMetadata(id: string) {
  const done = useClipInvalidate();
  return useMutation({
    mutationFn: (use_llm?: boolean) => api.post<JobOut>(`/clips/${id}/metadata/regenerate`, use_llm === undefined ? {} : { use_llm }),
    onSuccess: () => done(undefined, id),
  });
}

export async function getDownloadUrl(id: string) {
  return api.get<{ url: string; filename: string }>(`/clips/${id}/download`);
}

/**
 * Clip detail for list rows that need social_metadata (title/description/hashtags) when the list
 * endpoint does not embed it. Keyed on updated_at so it refreshes when the clip changes; no polling.
 */
export function useClipMeta(clip: ClipOut, enabled = true) {
  return useQuery({
    queryKey: ["clip", clip.id, "meta", clip.updated_at],
    queryFn: ({ signal }) => api.get<ClipDetail>(`/clips/${clip.id}`, undefined, signal),
    // skipped while the clip is still being generated (metadata is written after rendering)
    enabled: enabled && !clip.social_metadata && !["removed", "pending", "processing"].includes(clip.status ?? ""),
    staleTime: Infinity,
    retry: false,
  });
}
