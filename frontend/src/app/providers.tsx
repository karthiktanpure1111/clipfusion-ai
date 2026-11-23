"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError } from "@/lib/api/client";
import { toastError } from "@/lib/api/hooks/common";
import { TooltipProvider } from "@/components/ui/tooltip";

function makeClient() {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (err, query) => {
        if (query.meta?.toastError) toastError(err);
      },
    }),
    mutationCache: new MutationCache({
      onError: (err, _vars, _ctx, mutation) => {
        if (!mutation.meta?.silentError) toastError(err);
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: true,
        retry: (count, err) => {
          if (err instanceof ApiError && err.status >= 400 && err.status < 500) return false;
          if (err instanceof ApiError && err.status === 503) return false;
          return count < 2;
        },
      },
      mutations: { retry: false },
    },
  });
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(makeClient);
  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delay={250}>{children}</TooltipProvider>
    </QueryClientProvider>
  );
}
