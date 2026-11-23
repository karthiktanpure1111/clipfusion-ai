"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  AccountOut,
  CalendarEntry,
  NextOut,
  PreviewOut,
  QueueCreate,
  QueueItemOut,
  RequirementsOut,
  ScheduleInput,
  ScheduleOut,
} from "@/lib/api/types";
import { qk } from "./common";

// ------------------------------------------------------------------ accounts
export function useAccounts() {
  return useQuery({
    queryKey: qk.accounts,
    queryFn: ({ signal }) => api.get<AccountOut[]>("/accounts", undefined, signal),
  });
}

export function useRequirements() {
  return useQuery({
    queryKey: qk.requirements,
    queryFn: ({ signal }) => api.get<RequirementsOut>("/accounts/requirements", undefined, signal),
    staleTime: 5 * 60_000,
  });
}

export async function getConnectUrl(platform: string, includeDrive = false) {
  return api.get<{ authorization_url: string }>(`/accounts/connect/${platform}`, includeDrive ? { include_drive: true } : undefined);
}

export function useDisconnectAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/accounts/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.accounts }),
  });
}

export function useCheckAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<AccountOut>(`/accounts/${id}/check`),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.accounts }),
  });
}

// ------------------------------------------------------------------ queue / calendar
function useInvalidatePublishing() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["queue"] });
    qc.invalidateQueries({ queryKey: ["calendar"] });
    qc.invalidateQueries({ queryKey: qk.next });
    qc.invalidateQueries({ queryKey: ["clip"] });
    qc.invalidateQueries({ queryKey: qk.dashboard });
  };
}

export function useQueue(params: { clip_id?: string; status?: string } = {}, enabled = true) {
  return useQuery({
    queryKey: qk.queue(params),
    queryFn: ({ signal }) => api.get<QueueItemOut[]>("/publishing/queue", params, signal),
    enabled,
    refetchInterval: (q) =>
      q.state.data?.some((i) => ["publishing", "processing_remote", "rendering"].includes(i.status)) ? 4000 : false,
  });
}

export function useCalendar(start: string, end: string) {
  return useQuery({
    queryKey: qk.calendar(start, end),
    queryFn: ({ signal }) => api.get<CalendarEntry[]>("/publishing/calendar", { start, end }, signal),
    placeholderData: (prev) => prev,
    refetchInterval: 30_000,
  });
}

export function useNextPublish() {
  return useQuery({
    queryKey: qk.next,
    queryFn: ({ signal }) => api.get<NextOut>("/publishing/next", undefined, signal),
    refetchInterval: 60_000,
  });
}

export function useCreateQueue() {
  const inv = useInvalidatePublishing();
  return useMutation({
    mutationFn: (body: QueueCreate) => api.post<QueueItemOut[]>("/publishing/queue", body),
    onSuccess: inv,
  });
}

export function useUpdateQueueItem() {
  const inv = useInvalidatePublishing();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; scheduled_at?: string | null; payload?: Record<string, unknown> }) =>
      api.patch<QueueItemOut>(`/publishing/queue/${id}`, body),
    onSuccess: inv,
  });
}

export function useQueueAction() {
  const inv = useInvalidatePublishing();
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: "cancel" | "retry" | "publish-now" }) =>
      api.post<QueueItemOut>(`/publishing/queue/${id}/${action}`),
    onSuccess: inv,
  });
}

export function usePublishingPause() {
  const inv = useInvalidatePublishing();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (pause: boolean) => api.post<{ paused: boolean }>(pause ? "/publishing/pause" : "/publishing/resume"),
    onSuccess: () => {
      inv();
      qc.invalidateQueries({ queryKey: qk.me });
    },
  });
}

// ------------------------------------------------------------------ schedules
export function useSchedules() {
  return useQuery({
    queryKey: qk.schedules,
    queryFn: ({ signal }) => api.get<ScheduleOut[]>("/schedules", undefined, signal),
  });
}

export function useSchedulePreview(id: string | undefined, count = 8) {
  return useQuery({
    queryKey: [...qk.schedulePreview(id ?? ""), count],
    queryFn: ({ signal }) => api.get<PreviewOut>(`/schedules/${id}/preview`, { count }, signal),
    enabled: !!id,
  });
}

function useInvalidateSchedules() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: qk.schedules });
    qc.invalidateQueries({ queryKey: qk.next });
  };
}

export function useSaveSchedule() {
  const inv = useInvalidateSchedules();
  return useMutation({
    mutationFn: ({ id, body }: { id?: string; body: ScheduleInput }) =>
      id ? api.patch<ScheduleOut>(`/schedules/${id}`, body) : api.post<ScheduleOut>("/schedules", body),
    onSuccess: inv,
  });
}

export function useScheduleAction() {
  const inv = useInvalidateSchedules();
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: "pause" | "resume" | "delete" }) =>
      action === "delete" ? api.delete<unknown>(`/schedules/${id}`) : api.post<unknown>(`/schedules/${id}/${action}`),
    onSuccess: inv,
  });
}
