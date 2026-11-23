"use client";

import { useId } from "react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

/** Animated segmented control (single choice). */
export function Segmented<T extends string | number>({
  value,
  onChange,
  options,
  className,
  size = "default",
  ariaLabel,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: React.ReactNode; count?: number }[];
  className?: string;
  size?: "sm" | "default";
  ariaLabel: string;
}) {
  const id = useId();
  return (
    <div role="radiogroup" aria-label={ariaLabel} className={cn("inline-flex rounded-xl bg-white/4 p-1 ring-1 ring-white/8", className)}>
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={String(o.value)}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.value)}
            className={cn(
              "relative flex items-center gap-1.5 rounded-lg font-medium whitespace-nowrap transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring",
              size === "sm" ? "h-7 px-2.5 text-xs" : "h-8 px-3 text-sm",
              active ? "text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {active && (
              <motion.span
                layoutId={`seg-${id}`}
                className="absolute inset-0 rounded-lg bg-white/10 shadow-sm ring-1 ring-white/10"
                transition={{ type: "spring", stiffness: 500, damping: 38 }}
              />
            )}
            <span className="relative flex items-center gap-1.5">
              {o.label}
              {o.count !== undefined && (
                <span className="rounded-md bg-white/8 px-1.5 text-[10px] tabular-nums">{o.count}</span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}
