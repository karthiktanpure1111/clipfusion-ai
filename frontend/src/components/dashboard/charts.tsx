"use client";

import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { BarChart3, Clapperboard } from "lucide-react";
import { SectionCard } from "@/components/common/page";
import type { DashboardSummary } from "@/lib/api/types";
import { CHART, ChartTooltip, Legend, SrTable, axisProps, shortDate } from "./chart-kit";

function EmptyChart({ text }: { text: string }) {
  return (
    <div className="grid h-full place-items-center rounded-xl border border-dashed border-white/10 text-center text-sm text-muted-foreground">
      <p className="max-w-56">{text}</p>
    </div>
  );
}

export function ClipsPerDayChart({ data }: { data: DashboardSummary["clips_per_day"] }) {
  const total = data.reduce((s, d) => s + d.count, 0);
  return (
    <SectionCard title="Clips generated per day" description={`${total} clips in this period`} icon={Clapperboard}>
      <div className="h-56">
        {total === 0 ? (
          <EmptyChart text="No clips generated in this period yet. Add a video to see activity here." />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 6, right: 6, bottom: 0, left: -18 }}>
              <defs>
                <linearGradient id="clipsFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={CHART.primary} stopOpacity={0.45} />
                  <stop offset="100%" stopColor={CHART.primary} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke={CHART.grid} vertical={false} />
              <XAxis dataKey="date" tickFormatter={shortDate} minTickGap={24} {...axisProps} />
              <YAxis allowDecimals={false} width={40} {...axisProps} />
              <Tooltip content={<ChartTooltip />} cursor={{ stroke: "rgb(255 255 255 / 0.2)" }} />
              <Area type="monotone" dataKey="count" name="Clips" stroke={CHART.primary} strokeWidth={2} fill="url(#clipsFill)" activeDot={{ r: 4, strokeWidth: 2, stroke: "#0F0F12" }} />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
      <SrTable caption="Clips generated per day" headers={["Date", "Clips"]} rows={data.map((d) => [d.date, d.count])} />
    </SectionCard>
  );
}

export function PublishedPerDayChart({ data }: { data: DashboardSummary["published_per_day"] }) {
  const total = data.reduce((s, d) => s + d.youtube + d.instagram, 0);
  return (
    <SectionCard
      title="Published per day"
      description={`${total} successful publications`}
      icon={BarChart3}
      actions={
        <Legend
          items={[
            { label: "YouTube", color: CHART.youtube },
            { label: "Instagram", color: CHART.instagram },
          ]}
        />
      }
    >
      <div className="h-56">
        {total === 0 ? (
          <EmptyChart text="Nothing published yet. Connect a YouTube account and upload your clips." />
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 6, right: 6, bottom: 0, left: -18 }} barCategoryGap="28%">
              <CartesianGrid stroke={CHART.grid} vertical={false} />
              <XAxis dataKey="date" tickFormatter={shortDate} minTickGap={24} {...axisProps} />
              <YAxis allowDecimals={false} width={40} {...axisProps} />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: "rgb(255 255 255 / 0.04)" }} />
              <Bar dataKey="youtube" name="YouTube" stackId="p" fill={CHART.youtube} stroke="#16161b" strokeWidth={1} maxBarSize={22} />
              <Bar dataKey="instagram" name="Instagram" stackId="p" fill={CHART.instagram} stroke="#16161b" strokeWidth={1} radius={[4, 4, 0, 0]} maxBarSize={22} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
      <SrTable
        caption="Published clips per day by platform"
        headers={["Date", "YouTube", "Instagram"]}
        rows={data.map((d) => [d.date, d.youtube, d.instagram])}
      />
    </SectionCard>
  );
}
