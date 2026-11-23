import { AlertOctagon, AlertTriangle, Info } from "lucide-react";
import { cn } from "@/lib/utils";

import type { FramingWarning } from "@/lib/framing/types";

export const SEVERITY_STYLE: Record<FramingWarning["severity"], { label: string; className: string; bar: string }> = {
  critical: { label: "Critical", className: "bg-destructive/15 text-destructive ring-destructive/30", bar: "bg-destructive" },
  warning: { label: "Warning", className: "bg-warning/15 text-warning ring-warning/30", bar: "bg-warning" },
  info: { label: "Info", className: "bg-info/20 text-[#9aa2ff] ring-info/40", bar: "bg-info" },
};

export function WarningBadge({ severity, className }: { severity: FramingWarning["severity"]; className?: string }) {
  const s = SEVERITY_STYLE[severity];
  const Icon = severity === "critical" ? AlertOctagon : severity === "warning" ? AlertTriangle : Info;
  return (
    <span className={cn("inline-flex h-5 shrink-0 items-center gap-1 rounded-full px-1.5 text-[10px] font-semibold uppercase ring-1", s.className, className)}>
      <Icon className="size-3" aria-hidden />
      {s.label}
    </span>
  );
}
