"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Logo } from "@/components/common/brand";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { NAV_SECTIONS, activeNavHref } from "./nav";

export function SidebarNav({
  collapsed = false,
  onNavigate,
  layoutGroup = "desktop",
}: {
  collapsed?: boolean;
  onNavigate?: () => void;
  layoutGroup?: string;
}) {
  const pathname = usePathname();
  const active = activeNavHref(pathname);
  return (
    <nav aria-label="Main" className="flex flex-col gap-5">
      {NAV_SECTIONS.map((section) => (
        <div key={section.title}>
          {!collapsed && (
            <div className="mb-1.5 px-3 text-[10px] font-semibold tracking-[0.14em] text-muted-foreground/70 uppercase">
              {section.title}
            </div>
          )}
          <ul className="flex flex-col gap-0.5">
            {section.items.map((item) => {
              const isActive = item.href === active;
              const link = (
                <Link
                  href={item.href}
                  onClick={onNavigate}
                  aria-current={isActive ? "page" : undefined}
                  className={cn(
                    "group relative flex h-9 items-center gap-3 rounded-xl px-3 text-sm font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
                    isActive ? "text-white" : "text-sidebar-foreground/65 hover:bg-white/[0.04] hover:text-sidebar-foreground",
                    collapsed && "justify-center px-0",
                  )}
                >
                  {isActive && (
                    <motion.span
                      layoutId={`nav-active-${layoutGroup}`}
                      className="absolute inset-0 rounded-xl bg-gradient-to-r from-white/[0.09] to-white/[0.02] ring-1 ring-white/10"
                      transition={{ type: "spring", stiffness: 420, damping: 34 }}
                    >
                      <span className="absolute top-1.5 bottom-1.5 left-0 w-[3px] rounded-full bg-ig-gradient" />
                    </motion.span>
                  )}
                  <item.icon
                    className={cn(
                      "relative size-[18px] shrink-0 transition-colors",
                      isActive ? "text-[#ff4d6d]" : "text-muted-foreground group-hover:text-foreground",
                    )}
                  />
                  {!collapsed && <span className="relative truncate">{item.label}</span>}
                </Link>
              );
              return (
                <li key={item.href}>
                  {collapsed ? (
                    <Tooltip>
                      <TooltipTrigger render={<div />}>{link}</TooltipTrigger>
                      <TooltipContent side="right">{item.label}</TooltipContent>
                    </Tooltip>
                  ) : (
                    link
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

export function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  return (
    <aside
      className={cn(
        "sticky top-0 hidden h-dvh shrink-0 flex-col border-r border-sidebar-border bg-sidebar/80 backdrop-blur-xl transition-[width] duration-300 lg:flex",
        collapsed ? "w-[72px]" : "w-64",
      )}
    >
      <div className={cn("flex h-16 items-center px-4", collapsed && "justify-center px-0")}>
        <Link href="/app" className="rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring" aria-label="ClipFusion AI dashboard">
          <Logo collapsed={collapsed} />
        </Link>
      </div>
      <div className="flex-1 overflow-y-auto px-3 py-3">
        <SidebarNav collapsed={collapsed} />
      </div>
      <div className="border-t border-sidebar-border p-3">
        <button
          type="button"
          onClick={onToggle}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!collapsed}
          className={cn(
            "flex h-9 w-full items-center gap-3 rounded-xl px-3 text-sm text-muted-foreground transition-colors hover:bg-white/[0.04] hover:text-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring focus-visible:outline-none",
            collapsed && "justify-center px-0",
          )}
        >
          {collapsed ? <PanelLeftOpen className="size-[18px]" /> : <PanelLeftClose className="size-[18px]" />}
          {!collapsed && "Collapse"}
        </button>
      </div>
    </aside>
  );
}
