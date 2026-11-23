import {
  BarChart3,
  CalendarClock,
  Clapperboard,
  Cpu,
  Crop,
  LayoutDashboard,
  Library,
  Link2,
  Settings,
  UploadCloud,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  exact?: boolean;
}

export const NAV_SECTIONS: { title: string; items: NavItem[] }[] = [
  {
    title: "Create",
    items: [
      { href: "/app", label: "Dashboard", icon: LayoutDashboard, exact: true },
      { href: "/app/videos/new", label: "Add New Video", icon: UploadCloud, exact: true },
      { href: "/app/videos", label: "Videos", icon: Library },
      { href: "/app/processing", label: "AI Processing", icon: Cpu },
      { href: "/app/clips", label: "Generated Clips", icon: Clapperboard },
    ],
  },
  {
    title: "Edit",
    items: [
      { href: "/app/framing", label: "Smart Framing", icon: Crop },
    ],
  },
  {
    title: "Publish",
    items: [
      { href: "/app/calendar", label: "Publishing Calendar", icon: CalendarClock },
      { href: "/app/accounts", label: "Connected Accounts", icon: Link2 },
      { href: "/app/analytics", label: "Analytics", icon: BarChart3 },
      { href: "/app/settings", label: "Settings", icon: Settings },
    ],
  },
];

const ALL = NAV_SECTIONS.flatMap((s) => s.items);

/** The single nav item that best matches the path (longest prefix wins; /app/videos/new beats /app/videos). */
export function activeNavHref(pathname: string): string | null {
  let best: NavItem | null = null;
  for (const item of ALL) {
    const hit = item.exact ? pathname === item.href : pathname === item.href || pathname.startsWith(item.href + "/");
    if (hit && (!best || item.href.length > best.href.length)) best = item;
  }
  // the clip framing editor lives under /app/clips/[id]/framing: highlight the editor entry
  if (/^\/app\/clips\/[^/]+\/framing/.test(pathname)) return "/app/framing";
  return best?.href ?? null;
}

const EXTRA_TITLES: [RegExp, string][] = [
  [/^\/app\/clips\/[^/]+\/framing/, "Smart Framing Editor"],
  [/^\/app\/clips\/[^/]+$/, "Clip details"],
  [/^\/app\/videos\/(?!new)[^/]+$/, "Video details"],
];

export function pageTitle(pathname: string): string {
  for (const [re, title] of EXTRA_TITLES) if (re.test(pathname)) return title;
  const href = activeNavHref(pathname);
  return ALL.find((i) => i.href === href)?.label ?? "ClipFusion";
}
