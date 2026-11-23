"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, Plus, Settings } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { Logo } from "@/components/common/brand";
import { cn } from "@/lib/utils";
import { NotificationsBell } from "./notifications";
import { SidebarNav } from "./sidebar";
import { pageTitle } from "./nav";

export function Topbar() {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  return (
    <header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b border-white/6 bg-background/70 px-4 backdrop-blur-xl sm:px-6">
      <Button variant="ghost" size="icon" className="rounded-xl lg:hidden" aria-label="Open navigation" onClick={() => setMobileOpen(true)}>
        <Menu className="size-5" />
      </Button>
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="w-72 border-sidebar-border bg-sidebar p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <div className="flex h-16 items-center px-4">
            <Logo />
          </div>
          <div className="overflow-y-auto px-3 pb-6">
            <SidebarNav layoutGroup="mobile" onNavigate={() => setMobileOpen(false)} />
          </div>
        </SheetContent>
      </Sheet>
      {/* Pages render their own <h1>; this is the persistent location label. */}
      <p className="min-w-0 flex-1 truncate font-heading text-base font-semibold sm:text-lg">{pageTitle(pathname)}</p>
      <Link href="/app/videos/new" className={cn(buttonVariants({ size: "lg" }), "glow-red hidden rounded-xl px-3.5 sm:inline-flex")}>
        <Plus /> New video
      </Link>
      <Link href="/app/videos/new" aria-label="New video" className={cn(buttonVariants({ size: "icon" }), "rounded-xl sm:hidden")}>
        <Plus />
      </Link>
      <NotificationsBell />
      <Link href="/app/settings" aria-label="Settings" className={cn(buttonVariants({ variant: "ghost", size: "icon" }), "rounded-xl")}>
        <Settings className="size-[18px]" />
      </Link>
    </header>
  );
}
