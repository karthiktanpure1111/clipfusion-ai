"use client";

import { Suspense, useCallback, useState, useSyncExternalStore } from "react";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";

const COLLAPSE_KEY = "clipfusion.sidebarCollapsed";
const noopSubscribe = () => () => undefined;

function readCollapsed() {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

/** Sidebar preference: remembered per browser, "expanded" in the prerendered HTML. */
function useCollapsed(): [boolean, () => void] {
  const stored = useSyncExternalStore(noopSubscribe, readCollapsed, () => false);
  const [override, setOverride] = useState<boolean | null>(null);
  const collapsed = override ?? stored;
  const toggle = useCallback(() => {
    setOverride(!collapsed);
    try {
      window.localStorage.setItem(COLLAPSE_KEY, collapsed ? "0" : "1");
    } catch {
      /* storage unavailable: the preference just isn't remembered */
    }
  }, [collapsed]);
  return [collapsed, toggle];
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [collapsed, toggle] = useCollapsed();
  return (
    <div className="relative flex min-h-dvh">
      <div
        aria-hidden
        className="pointer-events-none fixed inset-x-0 top-0 h-80 bg-[radial-gradient(60%_100%_at_20%_0%,rgb(129_52_175/0.12),transparent),radial-gradient(50%_100%_at_90%_0%,rgb(255_0_51/0.08),transparent)]"
      />
      {/* usePathname suspends while prerendering dynamic routes, so each part has its own boundary */}
      <Suspense fallback={<aside className="hidden w-64 shrink-0 border-r border-sidebar-border bg-sidebar/80 lg:block" aria-hidden />}>
        <Sidebar collapsed={collapsed} onToggle={toggle} />
      </Suspense>
      <div className="relative flex min-w-0 flex-1 flex-col">
        <Suspense fallback={<div className="h-16 border-b border-white/6" aria-hidden />}>
          <Topbar />
        </Suspense>
        <main id="main" className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-6 lg:px-8 lg:py-8">
          {children}
        </main>
      </div>
    </div>
  );
}
