"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { AutoScheduleIn, AutoScheduleOut, TimelineCreate, TimelineOut } from "@/lib/api/types";
import { qk } from "./common";

const AUTO_KEY = ["auto-schedule"] as const;

function useInvalidateTimeline() {
  const qc = useQueryClient();
  return () => {
    for (const key of [["clips"], ["clip"], ["queue"], ["calendar"], qk.next, ["timeline-next"], AUTO_KEY, ["upload-runs"], qk.dashboard])
      qc.invalidateQueries({ queryKey: key });
  };
}

/** Schedule a video's clips one by one in timeline order, `gap_minutes` apart. */
export function useScheduleTimeline() {
  const inv = useInvalidateTimeline();
  return useMutation({
    mutationFn: (body: TimelineCreate) => api.post<TimelineOut>("/publishing/timeline", body),
    onSuccess: inv,
  });
}

/** Cancel every scheduled (not yet uploading) publication of a video. */
export function useClearTimeline() {
  const inv = useInvalidateTimeline();
  return useMutation({
    mutationFn: (videoSourceId: string) =>
      api.post<{ cancelled: number }>("/publishing/timeline/cancel", {
        video_source_id: videoSourceId,
      }),
    onSuccess: inv,
  });
}

/** Cancel one clip's scheduled upload, or upload it right away. */
export function useScheduledItemAction() {
  const inv = useInvalidateTimeline();
  return useMutation({
    mutationFn: ({ id, action }: { id: string; action: "cancel" | "publish-now" }) => api.post(`/publishing/queue/${id}/${action}`),
    onSuccess: inv,
  });
}

/** The upload times `count` posts would get on the account now (first right away when the channel is free). */
export function useTimelineNext(accountId: string | null | undefined, gapMinutes: number, count = 1) {
  return useQuery({
    queryKey: ["timeline-next", accountId, gapMinutes, count],
    queryFn: ({ signal }) =>
      api.get<{ next_at: string; slots: string[] }>(
        "/publishing/timeline/next",
        { account_id: accountId as string, gap_minutes: gapMinutes, count: Math.max(1, count) },
        signal,
      ),
    enabled: !!accountId,
    refetchInterval: 30_000,
  });
}

export function useAutoSchedule() {
  return useQuery({
    queryKey: AUTO_KEY,
    queryFn: ({ signal }) => api.get<AutoScheduleOut>("/publishing/auto-schedule", undefined, signal),
    refetchInterval: 60_000,
  });
}

export function useSaveAutoSchedule() {
  const qc = useQueryClient();
  const inv = useInvalidateTimeline();
  return useMutation({
    mutationFn: (body: AutoScheduleIn) => api.put<AutoScheduleOut>("/publishing/auto-schedule", body),
    onSuccess: (data) => {
      qc.setQueryData(AUTO_KEY, data);
      inv();
    },
  });
}
