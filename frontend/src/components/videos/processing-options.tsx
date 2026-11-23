"use client";

import { Gauge } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Segmented } from "@/components/common/segmented";
import type { AnalysisRequest, ProcessingMode, UserSettings } from "@/lib/api/types";
import { PROCESSING_MODES } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Defaults from the user's settings (GET /users/me), else the API defaults (balanced, 20–60 s, 10 clips). */
export function defaultAnalysis(settings?: Partial<UserSettings> | null): AnalysisRequest {
  const min = settings?.default_min_duration ?? 20;
  return {
    mode: settings?.default_processing_mode ?? "balanced",
    min_duration: min,
    max_duration: Math.max(min, settings?.default_max_duration ?? 60),
    max_clips: settings?.default_max_clips ?? 10,
  };
}

/** Human-readable problems with the options (empty when valid). */
export function analysisErrors(a: AnalysisRequest): string[] {
  const out: string[] = [];
  if (!(a.min_duration >= 5 && a.min_duration <= 180)) out.push("Minimum clip length must be 5–180 seconds.");
  if (!(a.max_duration >= a.min_duration && a.max_duration <= 180)) out.push("Maximum clip length must be between the minimum and 180 seconds.");
  if (!(Number.isInteger(a.max_clips) && a.max_clips >= 1 && a.max_clips <= 50)) out.push("Maximum clips must be a whole number from 1 to 50.");
  return out;
}

function NumberField({ id, label, value, onChange, min, max, suffix }: { id: string; label: string; value: number; onChange: (v: number) => void; min: number; max: number; suffix?: string }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="relative">
        <Input
          id={id}
          type="number"
          inputMode="numeric"
          min={min}
          max={max}
          value={Number.isFinite(value) ? value : ""}
          onChange={(e) => onChange(e.target.value === "" ? NaN : Number(e.target.value))}
          className={cn("h-9 rounded-xl", suffix && "pr-9")}
        />
        {suffix && <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-xs text-muted-foreground">{suffix}</span>}
      </div>
    </div>
  );
}

export function ProcessingOptions({ value, onChange, idPrefix = "proc", className }: { value: AnalysisRequest; onChange: (v: AnalysisRequest) => void; idPrefix?: string; className?: string }) {
  const set = (patch: Partial<AnalysisRequest>) => onChange({ ...value, ...patch });
  const mode = PROCESSING_MODES.find((m) => m.value === value.mode) ?? PROCESSING_MODES[1];
  const errors = analysisErrors(value);
  return (
    <fieldset className={cn("space-y-4", className)}>
      <legend className="mb-3 flex items-center gap-2 text-sm font-semibold">
        <Gauge className="size-4 text-muted-foreground" /> Processing options
      </legend>
      <div className="space-y-2">
        <Segmented<ProcessingMode>
          ariaLabel="Processing mode"
          value={value.mode}
          onChange={(m) => set({ mode: m })}
          options={PROCESSING_MODES.map((m) => ({ value: m.value, label: m.label }))}
        />
        <p className="text-xs text-muted-foreground" aria-live="polite">
          <span className="font-medium text-foreground/90">{mode.label}:</span> {mode.hint}
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <NumberField id={`${idPrefix}-min`} label="Min clip length" value={value.min_duration} min={5} max={180} suffix="s" onChange={(v) => set({ min_duration: v })} />
        <NumberField id={`${idPrefix}-max`} label="Max clip length" value={value.max_duration} min={5} max={180} suffix="s" onChange={(v) => set({ max_duration: v })} />
        <NumberField id={`${idPrefix}-clips`} label="Max clips" value={value.max_clips} min={1} max={50} onChange={(v) => set({ max_clips: v })} />
      </div>
      {errors.length > 0 && (
        <ul className="space-y-0.5 text-xs text-[#ff5c7a]" role="alert">
          {errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}
