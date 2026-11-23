"use client";

import { useSyncExternalStore } from "react";

const noopSubscribe = () => () => undefined;

/** Renders `fallback` in the prerendered HTML and `children` once the browser has hydrated. */
export function ClientOnly({ children, fallback = null }: { children: React.ReactNode; fallback?: React.ReactNode }) {
  const hydrated = useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  );
  return hydrated ? children : fallback;
}
