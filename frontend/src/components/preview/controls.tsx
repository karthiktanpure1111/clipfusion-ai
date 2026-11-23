"use client";

/** Small editor controls used by the Framing editor. */
import { useId, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";

import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export function Panel({ title, actions, children, className }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("glass rounded-2xl border border-white/[0.06] bg-card/70 p-4", className)}>
      {(title || actions) && (
        <header className="mb-3 flex items-center justify-between gap-2">
          {title && <h3 className="font-heading text-sm font-semibold tracking-tight">{title}</h3>}
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function LabeledSlider({
  label,
  value,
  min,
  max,
  step = 0.01,
  onChange,
  format = (v) => v.toFixed(2),
  hint,
  disabled,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
  format?: (v: number) => string;
  hint?: string;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <label htmlFor={id} className="font-medium text-foreground/90" title={hint}>
          {label}
        </label>
        <span className="tabular-nums text-muted-foreground">{format(value)}</span>
      </div>
      <Slider
        id={id}
        value={[value]}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onValueChange={(v) => onChange(Array.isArray(v) ? v[0] : v)}
        thumbAlignment="center"
        aria-label={label}
      />
      {hint && <p className="text-[11px] leading-snug text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  className,
}: {
  value: T;
  options: { value: T; label: ReactNode; title?: string }[];
  onChange: (v: T) => void;
  ariaLabel: string;
  className?: string;
}) {
  return (
    <div role="radiogroup" aria-label={ariaLabel} className={cn("inline-flex rounded-xl bg-muted p-0.5", className)}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          title={o.title}
          onClick={() => onChange(o.value)}
          className={cn(
            "flex flex-1 items-center justify-center gap-1 rounded-[10px] px-2.5 py-1 text-xs font-medium text-muted-foreground transition outline-none focus-visible:ring-2 focus-visible:ring-ring",
            value === o.value && "bg-ig-gradient text-white shadow",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function ToggleRow({
  label,
  description,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  description?: ReactNode;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <label htmlFor={id} className="text-xs font-medium">
          {label}
        </label>
        {description && <p className="text-[11px] leading-snug text-muted-foreground">{description}</p>}
      </div>
      <Switch id={id} checked={checked} disabled={disabled} onCheckedChange={(v) => onChange(v)} />
    </div>
  );
}

export function TipButton({
  label,
  shortcut,
  children,
  ...props
}: React.ComponentProps<typeof Button> & { label: string; shortcut?: string }) {
  return (
    <Tooltip>
      <TooltipTrigger render={<Button aria-label={label} {...props} />}>{children}</TooltipTrigger>
      <TooltipContent>
        {label}
        {shortcut && <kbd className="ml-1 rounded bg-background/20 px-1 font-mono text-[10px]">{shortcut}</kbd>}
      </TooltipContent>
    </Tooltip>
  );
}

const SWATCHES = ["#FFFFFF", "#000000", "#FF0033", "#FFD400", "#FCAF45", "#DD2A7B", "#8134AF", "#515BD4", "#22C55E", "#00E5FF"];
const HEX = /^#[0-9A-Fa-f]{6}$/;

export function ColorField({ label, value, onChange }: { label: string; value: string; onChange: (hex: string) => void }) {
  const id = useId();
  const [draft, setDraft] = useState<string | null>(null);
  const text = draft ?? value;
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-xs font-medium">
        {label}
      </label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          aria-label={`${label} picker`}
          value={HEX.test(value) ? value.toLowerCase() : "#000000"}
          onChange={(e) => onChange(e.target.value.toUpperCase())}
          className="size-8 shrink-0 cursor-pointer rounded-lg border border-white/10 bg-transparent p-0.5"
        />
        <input
          id={id}
          value={text}
          spellCheck={false}
          maxLength={7}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => {
            const v = text.startsWith("#") ? text : `#${text}`;
            if (HEX.test(v)) onChange(v.toUpperCase());
            setDraft(null);
          }}
          onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
          aria-invalid={!HEX.test(text.startsWith("#") ? text : `#${text}`)}
          className="h-8 w-24 rounded-lg border border-input bg-input/30 px-2 font-mono text-xs uppercase outline-none focus-visible:ring-2 focus-visible:ring-ring aria-invalid:border-destructive"
        />
        <div className="flex flex-wrap gap-1">
          {SWATCHES.map((c) => (
            <button
              key={c}
              type="button"
              aria-label={`Use ${c}`}
              onClick={() => onChange(c)}
              className={cn("size-5 rounded-md ring-1 ring-white/15 outline-none focus-visible:ring-2 focus-visible:ring-ring", value.toUpperCase() === c && "ring-2 ring-white")}
              style={{ background: c }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
