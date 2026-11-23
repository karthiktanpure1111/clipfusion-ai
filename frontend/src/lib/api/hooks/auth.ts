"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { UserOut, UserSettings } from "@/lib/api/types";
import { qk } from "./common";

/** The single local user (no-login mode): name, timezone and settings. */
export function useMe() {
  return useQuery({
    queryKey: qk.me,
    queryFn: ({ signal }) => api.get<UserOut>("/users/me", undefined, signal),
    staleTime: 60_000,
  });
}

/** The user's timezone (undefined until loaded; callers fall back to the browser zone). */
export function useUserTz(): string | undefined {
  const { data } = useMe();
  return data?.timezone || undefined;
}

export function useUpdateMe() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { full_name?: string; timezone?: string; settings?: Partial<UserSettings> }) =>
      api.patch<UserOut>("/users/me", body),
    onSuccess: (user) => {
      qc.setQueryData(qk.me, user);
      qc.invalidateQueries({ queryKey: qk.next });
    },
  });
}
