"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { AnalysisRequest, AnalyzeBody, Capabilities, JobOut, MediaUrlOut, Page, PipelineOut, SourceOut, StorageOut, TranscriptOut, YouTubeDownloadQuality, YouTubeQuality } from "@/lib/api/types";
import { qk } from "./common";

const BUSY: SourceOut["status"][] = ["importing", "queued", "processing"];
export const isSourceBusy = (s: Pick<SourceOut, "status" | "active_job">) => BUSY.includes(s.status) || !!s.active_job;

export interface SourceListParams {
  limit?: number;
  offset?: number;
  status?: string;
  q?: string;
}

export function useSources(params: SourceListParams = {}) {
  return useQuery({
    queryKey: qk.sources(params),
    queryFn: ({ signal }) => api.get<Page<SourceOut>>("/sources", { limit: 60, ...params }, signal),
    refetchInterval: (q) => (q.state.data?.items.some(isSourceBusy) ? 4000 : false),
  });
}

export function useSource(id: string | undefined) {
  return useQuery({
    queryKey: qk.source(id ?? ""),
    queryFn: ({ signal }) => api.get<SourceOut>(`/sources/${id}`, undefined, signal),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data && isSourceBusy(q.state.data) ? 3000 : false),
  });
}

export function useTranscript(id: string | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.transcript(id ?? ""),
    queryFn: ({ signal }) => api.get<TranscriptOut>(`/sources/${id}/transcript`, undefined, signal),
    enabled: !!id && enabled,
    retry: false,
  });
}

export function useMediaUrl(id: string | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.mediaUrl(id ?? ""),
    queryFn: ({ signal }) => api.get<MediaUrlOut>(`/sources/${id}/media-url`, undefined, signal),
    enabled: !!id && enabled,
    retry: false,
    // signed URLs are short-lived: refresh a little before they expire
    staleTime: (q) => Math.max(30_000, ((q.state.data?.expires_in ?? 600) - 60) * 1000),
  });
}

export function useCapabilities() {
  return useQuery({
    queryKey: qk.capabilities,
    queryFn: ({ signal }) => api.get<Capabilities>("/system/capabilities", undefined, signal),
    staleTime: 5 * 60_000,
  });
}

function useInvalidateSources() {
  const qc = useQueryClient();
  return (src?: SourceOut) => {
    qc.invalidateQueries({ queryKey: ["sources"] });
    qc.invalidateQueries({ queryKey: qk.dashboard });
    if (src) qc.setQueryData(qk.source(src.id), src);
  };
}

export function useCreateYoutubeSource() {
  const done = useInvalidateSources();
  return useMutation({
    mutationFn: (body: { url: string; rights_confirmed: true; attribution?: string }) => api.post<SourceOut>("/sources/youtube", body),
    onSuccess: (s) => done(s),
  });
}

/** Attach a creator-shared direct HTTPS file link to a source (YouTube/streaming hosts are refused). */
export function useAttachLink() {
  const done = useInvalidateSources();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; url: string; rights_confirmed: true; auto_analyze: boolean; analysis?: AnalysisRequest }) =>
      api.post<SourceOut>(`/sources/${id}/attach-link`, body),
    onSuccess: (s) => done(s),
  });
}

/** Qualities a YouTube-link video can be downloaded in (best first). Takes a few seconds the first time. */
export function useYouTubeQualities(id: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ["sources", id, "youtube-qualities"],
    queryFn: () => api.get<{ qualities: YouTubeQuality[] }>(`/sources/${id}/youtube-qualities`),
    enabled: !!id && enabled,
    staleTime: 30 * 60_000,
    retry: false,
  });
}

/** Download a YouTube-link video with yt-dlp (opt-in in Settings), then process it automatically. */
export function useDownloadYouTube() {
  const done = useInvalidateSources();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; quality?: YouTubeDownloadQuality | "480"; rights_confirmed: true; auto_analyze: boolean; analysis?: AnalysisRequest }) =>
      api.post<SourceOut>(`/sources/${id}/download`, body),
    onSuccess: (s) => done(s),
  });
}

export function useCreateCloudSource() {
  const done = useInvalidateSources();
  return useMutation({
    mutationFn: (body: {
      provider: "url" | "google_drive";
      url?: string;
      file_id?: string;
      title?: string;
      rights_confirmed: true;
      auto_analyze?: boolean;
      analysis?: AnalysisRequest;
    }) => api.post<SourceOut>("/sources/cloud", body),
    onSuccess: (s) => done(s),
  });
}

export function useAnalyzeSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: AnalyzeBody }) => api.post<JobOut>(`/sources/${id}/analyze`, body),
    onSuccess: (_job, { id }) => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: ["source", id] });
      qc.invalidateQueries({ queryKey: ["sources"] });
      qc.invalidateQueries({ queryKey: qk.dashboard });
    },
  });
}

export function useUpdateSource() {
  const done = useInvalidateSources();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; title?: string; description?: string }) =>
      api.patch<SourceOut>(`/sources/${id}`, body),
    onSuccess: (s) => done(s),
  });
}

export function useDeleteSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/sources/${id}`),
    onSuccess: (_r, id) => {
      qc.removeQueries({ queryKey: qk.source(id) });
      qc.invalidateQueries({ queryKey: ["sources"] });
      qc.invalidateQueries({ queryKey: ["clips"] });
      qc.invalidateQueries({ queryKey: qk.dashboard });
    },
  });
}

export function useRefreshRetention() {
  const done = useInvalidateSources();
  return useMutation({
    mutationFn: (id: string) => api.post<SourceOut>(`/sources/${id}/retention/refresh`),
    onSuccess: (s) => done(s),
  });
}

// ------------------------------------------------------------------ v2: pipeline, originals, storage
/** Not specified in API_V2.md; the backend returns {id, status: "deleted", freed_bytes, source}. */
export interface DeleteOriginalOut {
  id: string;
  status?: string;
  freed_bytes?: number | null;
  source?: SourceOut;
}

const PIPELINE_POLL_MS = 2000;

/**
 * Real per-stage processing status (GET /sources/{id}/pipeline); polls every ~2 s while running,
 * and while the source itself is busy (queued/importing) so the start of processing is picked up.
 */
export function usePipeline(id: string | undefined, sourceBusy = false) {
  return useQuery({
    queryKey: qk.pipeline(id ?? ""),
    queryFn: ({ signal }) => api.get<PipelineOut>(`/sources/${id}/pipeline`, undefined, signal),
    enabled: !!id,
    retry: false,
    refetchInterval: (q) => (q.state.data?.status === "running" || sourceBusy ? PIPELINE_POLL_MS : false),
  });
}

export function useStorage() {
  return useQuery({
    queryKey: qk.storage,
    queryFn: ({ signal }) => api.get<StorageOut>("/storage", undefined, signal),
    staleTime: 10_000,
    refetchInterval: 60_000,
  });
}

/** Delete only the original long video (and its proxies); clip records and links stay. */
export function useDeleteOriginal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<DeleteOriginalOut | undefined>(`/sources/${id}/delete-original`),
    onSuccess: (r, id) => {
      if (r?.source?.id) qc.setQueryData(qk.source(id), r.source);
      qc.invalidateQueries({ queryKey: ["source", id] });
      qc.invalidateQueries({ queryKey: ["sources"] });
      qc.invalidateQueries({ queryKey: qk.storage });
    },
  });
}
