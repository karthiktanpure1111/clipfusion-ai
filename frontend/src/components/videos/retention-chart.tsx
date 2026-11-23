"use client";

import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { LineChart as LineIcon, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SectionCard } from "@/components/common/page";
import { CHART, ChartTooltip, SrTable, axisProps } from "@/components/dashboard/chart-kit";
import { useRefreshRetention } from "@/lib/api/hooks/sources";
import type { RetentionPoint } from "@/lib/api/types";
import { formatTimestamp } from "@/lib/format";

/** REAL audience retention from the YouTube Analytics API (connected channel only). */
export function RetentionChart({ sourceId, points, duration }: { sourceId: string; points: RetentionPoint[]; duration: number | null }) {
  const refresh = useRefreshRetention();
  const data = points.map((p) => ({
    t: duration ? p.ratio * duration : p.ratio * 100,
    pct: Math.round(p.audience_watch_ratio * 1000) / 10,
  }));
  const fmt = (v: string | number) => (duration ? formatTimestamp(Number(v)) : `${Math.round(Number(v))}%`);
  return (
    <SectionCard
      title="YouTube Analytics — audience retention"
      description="Real data for your own video: share of viewers still watching at each point (can exceed 100% where people rewatch)."
      icon={LineIcon}
      actions={
        <Button variant="ghost" size="sm" onClick={() => refresh.mutate(sourceId)} disabled={refresh.isPending}>
          <RefreshCw className={refresh.isPending ? "animate-spin" : undefined} /> Refresh
        </Button>
      }
    >
      <div className="h-52">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 6, right: 8, bottom: 0, left: -12 }}>
            <defs>
              <linearGradient id="retFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={CHART.youtube} stopOpacity={0.35} />
                <stop offset="100%" stopColor={CHART.youtube} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke={CHART.grid} vertical={false} />
            <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} tickFormatter={fmt} {...axisProps} />
            <YAxis tickFormatter={(v) => `${v}%`} width={48} {...axisProps} />
            <Tooltip content={<ChartTooltip labelFormatter={(l) => `At ${fmt(l)}`} unit="%" />} />
            <Area type="monotone" dataKey="pct" name="Audience watching" stroke={CHART.youtube} strokeWidth={2} fill="url(#retFill)" />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">Source: YouTube Analytics API (audienceWatchRatio).</p>
      <SrTable caption="Audience retention" headers={["Position", "Audience watch ratio %"]} rows={data.map((d) => [fmt(d.t), d.pct])} />
    </SectionCard>
  );
}
