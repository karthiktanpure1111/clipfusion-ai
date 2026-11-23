"use client";

import { format, parseISO } from "date-fns";

export const CHART = {
  grid: "rgb(255 255 255 / 0.06)",
  axis: "#8b8b98",
  youtube: "#FF0033",
  instagram: "#D058C8",
  primary: "#D058C8",
  neutral: "#6b6b78",
  failed: "#C2790B",
};

export const axisProps = {
  stroke: CHART.axis,
  tick: { fill: CHART.axis, fontSize: 11 },
  tickLine: false,
  axisLine: false,
} as const;

export const shortDate = (d: string) => {
  try {
    return format(parseISO(d), "MMM d");
  } catch {
    return d;
  }
};

interface TooltipPayload {
  name?: string | number;
  value?: number | string;
  color?: string;
  dataKey?: string | number;
  payload?: Record<string, unknown>;
}

/** Dark glass tooltip for recharts. `labelFormatter` formats the x value. */
export function ChartTooltip({
  active,
  payload,
  label,
  labelFormatter = shortDate,
  unit,
  showTotal = true,
}: {
  active?: boolean;
  payload?: TooltipPayload[];
  label?: string | number;
  labelFormatter?: (l: string) => string;
  unit?: string;
  /** Sum the series (only meaningful for stacked values of the same kind). */
  showTotal?: boolean;
}) {
  if (!active || !payload?.length) return null;
  const total = showTotal && payload.length > 1 ? payload.reduce((s, p) => s + (Number(p.value) || 0), 0) : null;
  return (
    <div className="min-w-36 rounded-xl border border-white/10 bg-[#16161c]/95 px-3 py-2 text-xs shadow-xl backdrop-blur">
      <div className="mb-1.5 font-medium text-foreground">{labelFormatter(String(label ?? ""))}</div>
      <ul className="space-y-1">
        {payload.map((p) => (
          <li key={String(p.dataKey)} className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-1.5 text-muted-foreground">
              <span className="size-2 rounded-sm" style={{ background: p.color }} aria-hidden />
              {p.name}
            </span>
            <span className="font-medium text-foreground tabular-nums">
              {p.value}
              {unit}
            </span>
          </li>
        ))}
        {total !== null && (
          <li className="mt-1 flex justify-between gap-4 border-t border-white/10 pt-1 text-muted-foreground">
            Total <span className="font-medium text-foreground tabular-nums">{total}</span>
          </li>
        )}
      </ul>
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string }[] }) {
  return (
    <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {items.map((i) => (
        <li key={i.label} className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-sm" style={{ background: i.color }} aria-hidden />
          {i.label}
        </li>
      ))}
    </ul>
  );
}

/** Visually hidden data table for screen readers. */
export function SrTable({ caption, headers, rows }: { caption: string; headers: string[]; rows: (string | number)[][] }) {
  return (
    <table className="sr-only">
      <caption>{caption}</caption>
      <thead>
        <tr>
          {headers.map((h) => (
            <th key={h} scope="col">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            {r.map((c, j) => (
              <td key={j}>{c}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
