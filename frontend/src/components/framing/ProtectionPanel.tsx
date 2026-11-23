"use client";

/**
 * Crop protection: the live dry-run warnings (never hidden) with severity, message, affected
 * time ranges (click to seek), one-click fixes (config_patch deep-merged) and the
 * "I reviewed this framing" acknowledgement.
 */
import { CheckCircle2, Loader2, ShieldCheck, Wand2 } from "lucide-react";
import { cn } from "@/lib/utils";

import { Button } from "@/components/ui/button";
import { Panel } from "@/components/preview/controls";
import { formatTime } from "@/components/preview/player";
import { SEVERITY_STYLE, WarningBadge } from "@/components/preview/WarningBadge";
import { blockingWarnings, type FramingWarning } from "@/lib/framing/types";

export function ProtectionPanel({
  warnings,
  checking,
  checkError,
  acknowledged,
  acknowledging,
  onSeek,
  onApplyFix,
  onAcknowledge,
  trackName,
}: {
  warnings: FramingWarning[];
  checking: boolean;
  checkError: string | null;
  acknowledged: boolean;
  acknowledging: boolean;
  onSeek: (t: number) => void;
  onApplyFix: (patch: Record<string, unknown>, label: string) => void;
  onAcknowledge: () => void;
  trackName: (id: number) => string;
}) {
  const blocking = blockingWarnings(warnings);
  const sorted = [...warnings].sort((a, b) => rank(b.severity) - rank(a.severity));
  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <ShieldCheck className="size-4 text-ig-pink" /> Crop protection
          {checking && <Loader2 className="size-3.5 animate-spin text-muted-foreground" aria-label="Checking framing" />}
        </span>
      }
      actions={
        <span className={cn("text-[11px] font-medium", blocking.length ? "text-warning" : "text-success")} aria-live="polite">
          {blocking.length ? `${blocking.length} to review` : "No blocking issues"}
        </span>
      }
    >
      {checkError && <p className="mb-2 rounded-lg bg-destructive/10 p-2 text-xs text-destructive">Live check failed: {checkError}</p>}
      {sorted.length === 0 ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <CheckCircle2 className="size-4 text-success" /> Every tracked face and the main activity are inside the final frame.
        </p>
      ) : (
        <ul className="space-y-2">
          {sorted.map((w, i) => (
            <li key={`${w.code}-${w.track_id}-${i}`} className="relative overflow-hidden rounded-xl bg-muted/60 p-3 pl-4">
              <span className={cn("absolute inset-y-0 left-0 w-1", SEVERITY_STYLE[w.severity].bar)} aria-hidden />
              <div className="flex items-start gap-2">
                <WarningBadge severity={w.severity} />
                <p className="text-xs leading-snug">
                  {w.message}
                  {w.track_id !== null && <span className="text-muted-foreground"> ({trackName(w.track_id)})</span>}
                </p>
              </div>
              {(w.visible_fraction !== null || w.affected_fraction !== null) && (
                <p className="mt-1 text-[11px] text-muted-foreground">
                  {w.visible_fraction !== null && `Visible: ${Math.round(w.visible_fraction * 100)}% of the face. `}
                  {w.affected_fraction !== null && `Affects ${Math.round(w.affected_fraction * 100)}% of the clip.`}
                </p>
              )}
              {w.time_ranges.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {w.time_ranges.slice(0, 8).map(([a, b], j) => (
                    <button
                      key={j}
                      type="button"
                      onClick={() => onSeek(a)}
                      className="rounded-md bg-background/60 px-1.5 py-0.5 font-mono text-[10px] text-foreground/80 hover:bg-background focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                      aria-label={`Seek to ${formatTime(a, true)}`}
                    >
                      {formatTime(a, true)}–{formatTime(b, true)}
                    </button>
                  ))}
                </div>
              )}
              {w.suggestions.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {w.suggestions.map((s, j) => (
                    <Button key={j} size="xs" variant="secondary" onClick={() => onApplyFix(s.config_patch, s.label)}>
                      <Wand2 /> {s.label}
                    </Button>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {blocking.length > 0 && (
        <div className="mt-3 border-t border-white/[0.06] pt-3">
          {acknowledged ? (
            <p className="flex items-center gap-1.5 text-xs text-success">
              <CheckCircle2 className="size-4" /> You reviewed this framing — renders are allowed.
            </p>
          ) : (
            <Button variant="outline" size="sm" className="w-full" onClick={onAcknowledge} disabled={acknowledging || checking}>
              {acknowledging && <Loader2 className="animate-spin" />}I reviewed this framing
            </Button>
          )}
        </div>
      )}
    </Panel>
  );
}

const rank = (s: FramingWarning["severity"]) => (s === "critical" ? 2 : s === "warning" ? 1 : 0);
