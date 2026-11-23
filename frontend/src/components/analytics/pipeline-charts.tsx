"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { BarChart3, Clapperboard, Gauge } from "lucide-react";
import { SectionCard } from "@/components/common/page";
import { CHART, ChartTooltip, Legend, SrTable, axisProps, shortDate } from "@/components/dashboard/chart-kit";
import type { AnalyticsOverview } from "@/lib/api/types";
import { AI_SCORE_LABEL } from "@/lib/format";

type Series = AnalyticsOverview["internal"]["series"];

function Empty({ text }: { text: string }) {
  return <div className="grid h-full place-items-center rounded-xl border border-dashed border-white/10 px-4 text-center text-sm text-muted-foreground">{text}</div>;
}

const barCursor = { fill: "rgb(255 255 255 / 0.04)" };
const margin = { top: 6, right: 6, bottom: 0, left: -18 };

export function ClipsChart({ series }: { series: Series }) {
  const empty = series.every((d) => !d.clips_generated);
  return (
    <SectionCard title="Clips generated" icon={Clapperboard}>
      <div className="h-56">
        {empty ? (
          <Empty text="No clips were generated in this period." />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={series} margin={margin} barCategoryGap="28%">
              <CartesianGrid stroke={CHART.grid} vertical={false} />
              <XAxis dataKey="date" tickFormatter={shortDate} minTickGap={24} {...axisProps} />
              <YAxis allowDecimals={false} width={40} {...axisProps} />
              <Tooltip content={<ChartTooltip />} cursor={barCursor} />
              <Bar dataKey="clips_generated" name="Clips" fill={CHART.primary} radius={[4, 4, 0, 0]} maxBarSize={22} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
      <SrTable caption="Clips generated per day" headers={["Date", "Clips"]} rows={series.map((d) => [d.date, d.clips_generated])} />
    </SectionCard>
  );
}

export function PublishingChart({ series }: { series: Series }) {
  const empty = series.every((d) => !d.published_youtube && !d.published_instagram && !d.publish_failures);
  return (
    <SectionCard
      title="Publishing activity"
      icon={BarChart3}
      actions={
        <Legend
          items={[
            { label: "YouTube", color: CHART.youtube },
            { label: "Instagram", color: CHART.instagram },
            { label: "Failed", color: CHART.failed },
          ]}
        />
      }
    >
      <div className="h-56">
        {empty ? (
          <Empty text="No publishing activity in this period." />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={series} margin={margin} barCategoryGap="28%" barGap={2}>
              <CartesianGrid stroke={CHART.grid} vertical={false} />
              <XAxis dataKey="date" tickFormatter={shortDate} minTickGap={24} {...axisProps} />
              <YAxis allowDecimals={false} width={40} {...axisProps} />
              <Tooltip content={<ChartTooltip showTotal={false} />} cursor={barCursor} />
              <Bar dataKey="published_youtube" name="YouTube" stackId="published" fill={CHART.youtube} stroke="#16161b" strokeWidth={1} maxBarSize={22} />
              <Bar dataKey="published_instagram" name="Instagram" stackId="published" fill={CHART.instagram} stroke="#16161b" strokeWidth={1} maxBarSize={22} />
              <Bar dataKey="publish_failures" name="Failed attempts" fill={CHART.failed} radius={[4, 4, 0, 0]} maxBarSize={22} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
      <SrTable
        caption="Publishing activity per day"
        headers={["Date", "YouTube", "Instagram", "Failed attempts"]}
        rows={series.map((d) => [d.date, d.published_youtube, d.published_instagram, d.publish_failures])}
      />
    </SectionCard>
  );
}

export function ScoreHistogram({ data }: { data: AnalyticsOverview["ai_highlight_scores"] }) {
  const total = data.buckets.reduce((s, b) => s + b.count, 0);
  return (
    <SectionCard title={`${data.label || AI_SCORE_LABEL} distribution`} description="AI estimates — not viewership" icon={Gauge}>
      <div className="h-56">
        {!total ? (
          <Empty text="No scored clips in this period." />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data.buckets} margin={margin} barCategoryGap="14%">
              <CartesianGrid stroke={CHART.grid} vertical={false} />
              <XAxis dataKey="range" interval="preserveStartEnd" {...axisProps} />
              <YAxis allowDecimals={false} width={40} {...axisProps} />
              <Tooltip content={<ChartTooltip labelFormatter={(l) => `Score ${l} (estimate)`} />} cursor={barCursor} />
              <Bar dataKey="count" name="Clips" fill={CHART.primary} radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
      <p className="mt-3 text-xs text-muted-foreground">{data.note}</p>
      <SrTable caption="AI highlight score distribution (estimates)" headers={["Score range", "Clips"]} rows={data.buckets.map((b) => [b.range, b.count])} />
    </SectionCard>
  );
}
