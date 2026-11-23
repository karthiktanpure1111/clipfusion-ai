"use client";

import { useMemo, useState } from "react";
import { Check, ChevronsUpDown, Globe2, ShieldCheck, X } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Button } from "@/components/ui/button";
import { allTimezones, formatCountdown } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useNow } from "./use-now";

export const RIGHTS_TEXT = "I have the creator's permission, a licence, or other rights to reuse, edit and publish this video.";

export function RightsCheckbox({
  checked,
  onChange,
  id = "rights",
  label = RIGHTS_TEXT,
  className,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  id?: string;
  label?: string;
  className?: string;
}) {
  return (
    <label
      htmlFor={id}
      className={cn(
        "flex cursor-pointer items-start gap-3 rounded-xl border p-3 text-sm transition-colors",
        checked ? "border-success/30 bg-success/[0.05]" : "border-white/10 bg-white/[0.02] hover:bg-white/[0.04]",
        className,
      )}
    >
      <Checkbox id={id} checked={checked} onCheckedChange={(v) => onChange(v === true)} className="mt-0.5" aria-required />
      <span className="flex-1">
        <span className="font-medium">{label}</span>
        <span className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
          <ShieldCheck className="size-3.5" /> Required. ClipFusion only processes media you are authorised to use.
        </span>
      </span>
    </label>
  );
}

export function TimezoneSelect({
  value,
  onChange,
  id,
  className,
}: {
  value: string;
  onChange: (tz: string) => void;
  id?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const zones = useMemo(() => allTimezones(), []);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            id={id}
            variant="outline"
            role="combobox"
            aria-expanded={open}
            className={cn("h-9 w-full justify-between font-normal", className)}
          />
        }
      >
        <span className="flex min-w-0 items-center gap-2">
          <Globe2 className="text-muted-foreground" />
          <span className="truncate">{value || "Select timezone"}</span>
        </span>
        <ChevronsUpDown className="opacity-50" />
      </PopoverTrigger>
      <PopoverContent className="w-(--anchor-width) min-w-64 p-0" align="start">
        <Command>
          <CommandInput placeholder="Search timezones…" />
          <CommandList className="max-h-64">
            <CommandEmpty>No timezone found.</CommandEmpty>
            {zones.map((z) => (
              <CommandItem
                key={z}
                value={z}
                onSelect={() => {
                  onChange(z);
                  setOpen(false);
                }}
              >
                <Check className={cn("size-4", z === value ? "opacity-100" : "opacity-0")} />
                {z.replace(/_/g, " ")}
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

/** Tag / hashtag chips editor. Enter or comma adds; Backspace on empty removes the last chip. */
export function ChipsInput({
  value,
  onChange,
  prefix = "",
  placeholder,
  id,
  max,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  prefix?: string;
  placeholder?: string;
  id?: string;
  max?: number;
}) {
  const [draft, setDraft] = useState("");
  const add = (raw: string) => {
    const items = raw
      .split(/[,\n]/)
      .map((s) => s.trim().replace(/^#/, ""))
      .filter(Boolean);
    if (!items.length) return;
    const next = [...value];
    for (const it of items) if (!next.includes(it) && (!max || next.length < max)) next.push(it);
    onChange(next);
    setDraft("");
  };
  return (
    <div className="flex min-h-9 flex-wrap items-center gap-1.5 rounded-lg border border-input bg-input/30 px-2 py-1.5 focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50">
      {value.map((tag) => (
        <span key={tag} className="inline-flex items-center gap-1 rounded-md bg-white/[0.06] px-2 py-0.5 text-xs ring-1 ring-white/10">
          {prefix}
          {tag}
          <button
            type="button"
            className="rounded text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            onClick={() => onChange(value.filter((t) => t !== tag))}
            aria-label={`Remove ${tag}`}
          >
            <X className="size-3" />
          </button>
        </span>
      ))}
      <input
        id={id}
        value={draft}
        onChange={(e) => (e.target.value.includes(",") ? add(e.target.value) : setDraft(e.target.value))}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add(draft);
          } else if (e.key === "Backspace" && !draft && value.length) {
            onChange(value.slice(0, -1));
          }
        }}
        onBlur={() => add(draft)}
        placeholder={value.length ? "" : placeholder}
        className="min-w-24 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
      />
      {max && <span className="text-[10px] text-muted-foreground tabular-nums">{value.length}/{max}</span>}
    </div>
  );
}

/** Live countdown to an ISO time. */
export function Countdown({ to, className }: { to: string | null | undefined; className?: string }) {
  const now = useNow();
  if (!to) return <span className={className}>–</span>;
  if (!now) return <span className={className}>…</span>;
  return (
    <span className={cn("tabular-nums", className)} suppressHydrationWarning>
      {formatCountdown(new Date(to).getTime() - now)}
    </span>
  );
}
