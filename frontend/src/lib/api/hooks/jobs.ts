"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { DashboardSummary, JobOut, Page } from "@/lib/api/types";
import { isJobActive, qk } from "./common";

export interface JobListParams {
  status?: string;
  job_type?: string;
  video_source_id?: string;
  clip_id?: string;
  active?: boolean;
  limit?: number;
  offset?: number;
}

export function useJobs(params: JobListParams = {}) {
  return useQuery({
    queryKey: qk.jobs(params),
    queryFn: ({ signal }) => api.get<Page<JobOut>>("/jobs", { limit: 100, ...params }, signal),
    refetchInterval: (q) => (q.state.data?.items.some((j) => isJobActive(j.status)) ? 2000 : 15_000),
  });
}

function useJobMutation(action: "cancel" | "retry") {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<JobOut>(`/jobs/${id}/${action}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      qc.invalidateQueries({ queryKey: qk.dashboard });
    },
  });
}
export const useCancelJob = () => useJobMutation("cancel");
export const useRetryJob = () => useJobMutation("retry");

export function useDashboard() {
  return useQuery({
    queryKey: qk.dashboard,
    queryFn: ({ signal }) => api.get<DashboardSummary>("/dashboard/summary", undefined, signal),
    refetchInterval: (q) => (q.state.data?.active_jobs.length ? 4000 : 30_000),
  });
}
