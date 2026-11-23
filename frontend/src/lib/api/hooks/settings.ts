"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type {
  AnalyticsOverview,
  AssetKind,
  AssetOut,
  FramingPresetOut,
  NotificationsOut,
} from "@/lib/api/types";
import { qk } from "./common";

// ------------------------------------------------------------------ notifications
export function useNotifications() {
  return useQuery({
    queryKey: qk.notifications,
    queryFn: ({ signal }) => api.get<NotificationsOut>("/notifications", { limit: 20 }, signal),
    refetchInterval: 30_000,
  });
}

export function useMarkNotification() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string | "all") =>
      id === "all" ? api.post<unknown>("/notifications/read-all") : api.post<unknown>(`/notifications/${id}/read`),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.notifications }),
  });
}

// ------------------------------------------------------------------ analytics
export function useAnalytics(days: number) {
  return useQuery({
    queryKey: qk.analytics(days),
    queryFn: ({ signal }) => api.get<AnalyticsOverview>("/analytics/overview", { days }, signal),
    placeholderData: (prev) => prev,
  });
}

export function useRefreshAnalytics() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ job_id: string; status: string; created: boolean }>("/analytics/refresh"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
      setTimeout(() => qc.invalidateQueries({ queryKey: ["analytics"] }), 5000);
    },
  });
}

// ------------------------------------------------------------------ presets
export function useFramingPresets() {
  return useQuery({
    queryKey: qk.framingPresets,
    queryFn: ({ signal }) => api.get<FramingPresetOut[]>("/framing-presets", undefined, signal),
  });
}

export function usePresetMutation() {
  const qc = useQueryClient();
  const base = "/framing-presets";
  const key = qk.framingPresets;
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: { name?: string; is_default?: boolean } | null }) =>
      patch === null ? api.delete(`${base}/${id}`) : api.patch(`${base}/${id}`, patch),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
  });
}

// ------------------------------------------------------------------ assets
export function useAssets() {
  return useQuery({
    queryKey: qk.assets,
    queryFn: ({ signal }) => api.get<AssetOut[]>("/assets", undefined, signal),
  });
}

export function useUploadAsset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, kind, name }: { file: File; kind: AssetKind; name?: string }) => {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("kind", kind);
      if (name) fd.append("name", name);
      return api.post<AssetOut>("/assets", fd);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.assets }),
  });
}

export function useDeleteAsset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/assets/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.assets }),
  });
}
