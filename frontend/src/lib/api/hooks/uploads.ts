"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { UploadRunCreate, UploadRunOut } from "@/lib/api/types";
import { qk } from "./common";

const RUN_POLL_MS = 2000;

/** Newest first; created_at is ISO so string comparison is chronological. */
const newestFirst = (runs: UploadRunOut[]) => [...runs].sort((a, b) => b.created_at.localeCompare(a.created_at));

/**
 * Upload runs of one video (GET /upload-runs?video_source_id=), newest first. Polls every ~2 s while
 * a run is running. Finished/failed runs are included so the last result (and Retry) stay visible.
 */
export function useUploadRuns(sourceId: string | undefined) {
  return useQuery({
    queryKey: qk.uploadRuns(sourceId ?? ""),
    queryFn: async ({ signal }) => newestFirst(await api.get<UploadRunOut[]>("/upload-runs", { video_source_id: sourceId }, signal)),
    enabled: !!sourceId,
    retry: false,
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === "running") ? RUN_POLL_MS : false),
  });
}

function useRunInvalidate() {
  const qc = useQueryClient();
  return (run?: UploadRunOut) => {
    if (run) {
      qc.setQueryData<UploadRunOut[]>(qk.uploadRuns(run.video_source_id), (cur) =>
        newestFirst([run, ...(cur ?? []).filter((r) => r.id !== run.id)]),
      );
      qc.invalidateQueries({ queryKey: qk.uploadRuns(run.video_source_id) });
    } else {
      qc.invalidateQueries({ queryKey: ["upload-runs"] });
    }
    qc.invalidateQueries({ queryKey: ["clips"] });
    qc.invalidateQueries({ queryKey: ["clip"] });
    qc.invalidateQueries({ queryKey: qk.storage });
    qc.invalidateQueries({ queryKey: qk.dashboard });
  };
}

/** Start a sequential run. 409 `upload_in_progress` is handled by the caller (silent toast). */
export function useCreateUploadRun() {
  const done = useRunInvalidate();
  return useMutation({
    mutationFn: (body: UploadRunCreate) => api.post<UploadRunOut>("/upload-runs", body),
    onSuccess: (r) => done(r),
    meta: { silentError: true },
  });
}

export function useUploadRunAction() {
  const done = useRunInvalidate();
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: "retry" | "cancel" }) => api.post<UploadRunOut>(`/upload-runs/${id}/${action}`),
    onSuccess: (r) => done(r),
  });
}
