"use client";

import { LayoutGrid, List, Search, SlidersHorizontal } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { Segmented } from "@/components/common/segmented";
import { useSources } from "@/lib/api/hooks/sources";

export interface ClipFilterState {
  q: string;
  source: string;
  status: string;
  render: string;
  minScore: number;
  sort: "sequence" | "score" | "created";
  view: "grid" | "list";
}

const STATUS = [
  { value: "all", label: "Any status" },
  { value: "processing", label: "Processing" },
  { value: "generated", label: "Generated (ready to upload)" },
  { value: "uploading", label: "Uploading" },
  { value: "uploaded", label: "Uploaded" },
  { value: "upload_failed", label: "Upload failed" },
  { value: "cleaned", label: "Cleaned (files deleted)" },
];
const RENDER = [
  { value: "all", label: "Any render status" },
  { value: "not_rendered", label: "Not rendered" },
  { value: "queued", label: "Queued" },
  { value: "rendering", label: "Rendering" },
  { value: "rendered", label: "Rendered" },
  { value: "stale", label: "Render outdated" },
  { value: "failed", label: "Failed" },
];
const SORT = [
  { value: "sequence", label: "Timeline order" },
  { value: "score", label: "Highest AI score" },
  { value: "created", label: "Newest" },
];

function FilterSelect({ label, items, value, onChange, className }: { label: string; items: { value: string; label: string }[]; value: string; onChange: (v: string) => void; className?: string }) {
  return (
    <Select items={items} value={value} onValueChange={(v) => onChange(String(v ?? "all"))}>
      <SelectTrigger className={className ?? "h-9 w-full rounded-xl"} aria-label={label}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {items.map((i) => (
          <SelectItem key={i.value} value={i.value}>
            {i.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export function ClipFilters({ value, onChange }: { value: ClipFilterState; onChange: (patch: Partial<ClipFilterState>) => void }) {
  const sources = useSources({ limit: 100 });
  const sourceItems = [
    { value: "all", label: "All videos" },
    ...(sources.data?.items ?? []).map((s) => ({ value: s.id, label: s.title || s.original_filename || "Untitled video" })),
  ];
  if (value.source !== "all" && !sourceItems.some((s) => s.value === value.source)) sourceItems.push({ value: value.source, label: "Selected video" });

  return (
    <div className="space-y-3 rounded-2xl border border-white/7 bg-card/50 p-3 sm:p-4">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-center">
        <div className="relative flex-1">
          <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={value.q} onChange={(e) => onChange({ q: e.target.value })} placeholder="Search titles, transcripts, reasons…" aria-label="Search clips" className="h-9 rounded-xl pl-9" />
        </div>
        <div className="flex gap-2">
          <FilterSelect label="Sort clips" items={SORT} value={value.sort} onChange={(v) => onChange({ sort: v as ClipFilterState["sort"] })} className="h-9 w-full rounded-xl lg:w-48" />
          <Segmented
            ariaLabel="Layout"
            value={value.view}
            onChange={(v) => onChange({ view: v })}
            options={[
              { value: "grid", label: <LayoutGrid className="size-4" aria-label="Grid" /> },
              { value: "list", label: <List className="size-4" aria-label="List" /> },
            ]}
          />
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-[1.3fr_1fr_1fr_1.2fr]">
        <FilterSelect label="Filter by video" items={sourceItems} value={value.source} onChange={(v) => onChange({ source: v })} />
        <FilterSelect label="Filter by status" items={STATUS} value={value.status} onChange={(v) => onChange({ status: v })} />
        <FilterSelect label="Filter by render status" items={RENDER} value={value.render} onChange={(v) => onChange({ render: v })} />
        <div className="flex h-9 items-center gap-3 rounded-xl border border-input bg-input/30 px-3">
          <SlidersHorizontal className="size-4 shrink-0 text-muted-foreground" />
          <span id="min-score-label" className="shrink-0 text-xs text-muted-foreground">
            Min AI score (est.)
          </span>
          <Slider
            aria-labelledby="min-score-label"
            min={0}
            max={100}
            step={5}
            value={[value.minScore]}
            onValueChange={(v) => onChange({ minScore: Array.isArray(v) ? v[0] : v })}
            className="flex-1 [&_[data-slot=slider-track]]:bg-white/15"
          />
          <span className="w-6 text-right text-xs tabular-nums">{value.minScore}</span>
        </div>
      </div>
    </div>
  );
}
