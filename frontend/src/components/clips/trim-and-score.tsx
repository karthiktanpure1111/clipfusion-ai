"use client";

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Gauge, LineChart, Loader2, Minus, Plus, RotateCcw, Scissors } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SectionCard } from "@/components/common/page";
import { ScoreRing } from "@/components/common/score-ring";
import { CHART, ChartTooltip, SrTable, axisProps } from "@/components/dashboard/chart-kit";
import { useUpdateClip } from "@/lib/api/hooks/clips";
import type { ClipDetail } from "@/lib/api/types";
import { AI_SCORE_LABEL, formatPercent, formatTimestamp, titleCase } from "@/lib/format";

function TimeField({ id, label, value, onChange, min, max }: { id: string; label: string; value: number; onChange: (v: number) => void; min: number; max: number }) {
  const clamp = (v: number) => Math.round(Math.min(max, Math.max(min, v)) * 10) / 10;
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <div className="flex items-center gap-1">
        <Button type="button" variant="outline" size="icon" aria-label={`${label} −0.5s`} onClick={() => onChange(clamp(value - 0.5))}>
          <Minus />
        </Button>
        <Input id={id} type="number" step={0.1} value={value} onChange={(e) => onChange(clamp(Number(e.target.value)))} className="h-8 w-24 text-center tabular-nums" />
        <Button type="button" variant="outline" size="icon" aria-label={`${label} +0.5s`} onClick={() => onChange(clamp(value + 0.5))}>
          <Plus />
        </Button>
      </div>
      <p className="text-[11px] text-muted-foreground tabular-nums">{formatTimestamp(value, true)}</p>
    </div>
  );
}

export function TrimEditor({ clip }: { clip: ClipDetail }) {
  const update = useUpdateClip(clip.id);
  const [start, setStart] = useState(clip.start_time);
  const [end, setEnd] = useState(clip.end_time);
  const max = clip.source.duration ?? clip.end_time + 600;
  const dirty = Math.abs(start - clip.start_time) > 0.05 || Math.abs(end - clip.end_time) > 0.05;
  const dur = end - start;
  const invalid = dur < 1 || dur > 180;

  return (
    <SectionCard title="Trim" description="Fine-tune where the clip starts and ends in the original video." icon={Scissors}>
      <div className="flex flex-wrap items-end gap-5">
        <TimeField id="trim-start" label="Start (s)" value={start} onChange={setStart} min={0} max={end - 1} />
        <TimeField id="trim-end" label="End (s)" value={end} onChange={setEnd} min={start + 1} max={max} />
        <div className="pb-5">
          <div className="text-[11px] tracking-wide text-muted-foreground uppercase">Duration</div>
          <div className={`font-heading text-xl font-bold tabular-nums ${invalid ? "text-[#ff5c7a]" : ""}`}>{dur.toFixed(1)}s</div>
        </div>
      </div>
      <div className="mt-4 flex gap-2">
        <Button
          disabled={!dirty || invalid || update.isPending}
          onClick={() =>
            update.mutateAsync({ start_time: start, end_time: end }).then(
              () => toast.success("Trim saved", { description: clip.output_url ? "Render again to update the exported video." : undefined }),
              () => undefined,
            )
          }
        >
          {update.isPending && <Loader2 className="animate-spin" />} Save trim
        </Button>
        <Button
          variant="ghost"
          disabled={!dirty}
          onClick={() => {
            setStart(clip.start_time);
            setEnd(clip.end_time);
          }}
        >
          <RotateCcw /> Reset
        </Button>
      </div>
    </SectionCard>
  );
}

export function ScorePanel({ clip }: { clip: ClipDetail }) {
  const data = Object.entries(clip.score_breakdown ?? {})
    .map(([k, v]) => ({ name: titleCase(k), value: Math.round(v * 10) / 10 }))
    .sort((a, b) => b.value - a.value);
  return (
    <SectionCard title={AI_SCORE_LABEL} description="Estimated from transcript, audio and visual signals. Not a view count or audience retention." icon={Gauge}>
      <div className="flex items-center gap-4">
        <ScoreRing score={clip.highlight_score} size={64} />
        <div className="text-sm text-muted-foreground">
          Detection: <span className="text-foreground">{clip.detection_method === "signals+llm" ? "signals + LLM ranking" : clip.detection_method === "manual" ? "manual clip" : "signals only (no LLM)"}</span>
        </div>
      </div>
      {data.length > 0 && (
        <div className="mt-4" style={{ height: Math.max(120, data.length * 30) }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} layout="vertical" margin={{ top: 0, right: 16, bottom: 0, left: 0 }}>
              <CartesianGrid stroke={CHART.grid} horizontal={false} />
              <XAxis type="number" {...axisProps} />
              <YAxis type="category" dataKey="name" width={120} {...axisProps} />
              <Tooltip content={<ChartTooltip labelFormatter={(l) => `${l} (estimate)`} />} cursor={{ fill: "rgb(255 255 255 / 0.04)" }} />
              <Bar dataKey="value" name="Contribution" radius={[0, 4, 4, 0]} maxBarSize={16}>
                {data.map((d) => (
                  <Cell key={d.name} fill={CHART.primary} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <SrTable caption="AI score breakdown (estimate)" headers={["Signal", "Contribution"]} rows={data.map((d) => [d.name, d.value])} />
        </div>
      )}
      {clip.reasons.length > 0 && (
        <div className="mt-4">
          <h3 className="mb-1.5 text-xs font-medium tracking-wide text-muted-foreground uppercase">Why this moment</h3>
          <ul className="space-y-1 text-sm">
            {clip.reasons.map((r) => (
              <li key={r} className="flex gap-2">
                <span className="mt-2 size-1.5 shrink-0 rounded-full bg-ig-gradient" /> {r}
              </li>
            ))}
          </ul>
        </div>
      )}
      {clip.retention_score != null && (
        <div className="mt-4 flex items-start gap-3 rounded-xl border border-success/25 bg-success/5 p-3">
          <LineChart className="mt-0.5 size-4 text-success" />
          <div className="text-sm">
            <div className="font-medium">Real audience retention: {formatPercent(clip.retention_score)}</div>
            <div className="text-xs text-muted-foreground">
              Mean audience watch ratio over this range in the original video · Source: YouTube Analytics API (connected channel only)
            </div>
          </div>
        </div>
      )}
    </SectionCard>
  );
}
