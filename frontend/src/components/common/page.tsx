"use client";

import { motion } from "framer-motion";
import { AlertTriangle, PlugZap, RefreshCw } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api/client";
import { cn } from "@/lib/utils";

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  eyebrow?: React.ReactNode;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
      className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between"
    >
      <div className="min-w-0">
        {eyebrow && <div className="mb-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">{eyebrow}</div>}
        <h1 className="font-heading text-2xl font-bold tracking-tight sm:text-[28px]">{title}</h1>
        {description && <p className="mt-1.5 max-w-2xl text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </motion.div>
  );
}

export function Surface({ className, children, ...props }: React.ComponentProps<"div">) {
  return (
    <div className={cn("rounded-2xl border border-white/[0.07] bg-card/70 shadow-sm backdrop-blur-sm", className)} {...props}>
      {children}
    </div>
  );
}

export function SectionCard({
  title,
  description,
  icon: Icon,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: React.ReactNode;
  description?: React.ReactNode;
  icon?: LucideIcon;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <Surface className={cn("flex flex-col", className)}>
      {(title || actions) && (
        <div className="flex items-start justify-between gap-3 border-b border-white/[0.06] px-5 py-4">
          <div className="flex min-w-0 items-start gap-3">
            {Icon && (
              <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg bg-white/[0.04] ring-1 ring-white/10">
                <Icon className="size-4 text-muted-foreground" />
              </span>
            )}
            <div className="min-w-0">
              <h2 className="font-heading text-[15px] font-semibold">{title}</h2>
              {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
            </div>
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={cn("flex-1 p-5", bodyClassName)}>{children}</div>
    </Surface>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon: LucideIcon;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      className={cn("flex flex-col items-center justify-center px-6 py-14 text-center", className)}
    >
      <div className="gradient-border relative mb-4 grid size-14 place-items-center rounded-2xl bg-white/[0.03]">
        <Icon className="size-6 text-foreground/80" />
      </div>
      <h3 className="font-heading text-base font-semibold">{title}</h3>
      {description && <p className="mt-1.5 max-w-sm text-sm text-muted-foreground">{description}</p>}
      {action && <div className="mt-5 flex flex-wrap justify-center gap-2">{action}</div>}
    </motion.div>
  );
}

/** Error panel; 503 / integration_not_configured gets an explanatory "not configured" state. */
export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const api = error instanceof ApiError ? error : null;
  const notConfigured = api && (api.status === 503 || api.code === "integration_not_configured");
  const Icon = notConfigured ? PlugZap : AlertTriangle;
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center gap-3 rounded-2xl border px-6 py-10 text-center",
        notConfigured ? "border-warning/25 bg-warning/[0.04]" : "border-destructive/25 bg-destructive/[0.04]",
        className,
      )}
    >
      <Icon className={cn("size-6", notConfigured ? "text-warning" : "text-[#ff5c7a]")} />
      <div>
        <p className="font-medium">{notConfigured ? "Not configured on this server" : "Couldn’t load this"}</p>
        <p className="mt-1 max-w-md text-sm text-muted-foreground">
          {api?.message ?? (error instanceof Error ? error.message : "Unexpected error")}
        </p>
      </div>
      {onRetry && !notConfigured && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw /> Try again
        </Button>
      )}
    </div>
  );
}

export function CardGridSkeleton({ count = 6, className }: { count?: number; className?: string }) {
  return (
    <div className={cn("grid gap-4 sm:grid-cols-2 lg:grid-cols-3", className)} aria-busy aria-label="Loading">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="overflow-hidden rounded-2xl border border-white/[0.06] bg-card/60">
          <Skeleton className="aspect-video rounded-none" />
          <div className="space-y-2 p-4">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-3 w-1/2" />
          </div>
        </div>
      ))}
    </div>
  );
}

export const listVariants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.045 } },
};
export const itemVariants = {
  hidden: { opacity: 0, y: 10 },
  show: { opacity: 1, y: 0, transition: { duration: 0.28, ease: "easeOut" as const } },
};
