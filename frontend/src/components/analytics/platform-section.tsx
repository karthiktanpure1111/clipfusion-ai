"use client";

import Link from "next/link";
import { CalendarClock, Trophy } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PLATFORM_META, PlatformIcon } from "@/components/common/brand";
import { EmptyState, SectionCard, Surface } from "@/components/common/page";
import type { AnalyticsOverview, MetricTotals, Platform } from "@/lib/api/types";
import { formatCount, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

const METRICS: { key: keyof MetricTotals; label: string }[] = [
  { key: "views", label: "Views" },
  { key: "likes", label: "Likes" },
  { key: "comments", label: "Comments" },
  { key: "shares", label: "Shares" },
  { key: "saves", label: "Saves" },
  { key: "reach", label: "Reach" },
];

function PlatformCard({ platform, data }: { platform: Platform; data: AnalyticsOverview["platform"][Platform] }) {
  const reported = METRICS.filter((m) => data.totals[m.key] != null);
  return (
    <Surface className="p-5">
      <div className="flex items-center gap-3">
        <PlatformIcon platform={platform} className="size-6" />
        <div className="min-w-0 flex-1">
          <h3 className="font-heading font-semibold">{PLATFORM_META[platform].product}</h3>
          <p className="text-xs text-muted-foreground">Source: {data.source}</p>
        </div>
      </div>
      {data.publications_tracked === 0 ? (
        <p className="mt-4 rounded-xl border border-dashed border-white/10 px-4 py-6 text-center text-sm text-muted-foreground">
          No {PLATFORM_META[platform].label} statistics yet. They appear after a clip is published and refreshed.
        </p>
      ) : (
        <>
          <dl className="mt-4 grid grid-cols-3 gap-x-3 gap-y-4">
            {reported.map((m) => (
              <div key={m.key}>
                <dt className="text-xs text-muted-foreground">{m.label}</dt>
                <dd className="font-heading text-xl font-bold tabular-nums">{formatCount(data.totals[m.key])}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-4 text-xs text-muted-foreground">
            {data.publications_tracked} publication{data.publications_tracked === 1 ? "" : "s"} tracked
            {data.last_fetched_at && ` · updated ${formatRelative(data.last_fetched_at)}`}
            {reported.length < METRICS.length && " · metrics the platform didn't report are hidden"}
          </p>
        </>
      )}
    </Surface>
  );
}

function TopClips({ clips }: { clips: AnalyticsOverview["top_published_clips"] }) {
  return (
    <SectionCard title="Top published clips" description="Ranked by views reported by each platform. Totals since publication." icon={Trophy} bodyClassName="p-0">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="pl-5">Clip</TableHead>
            <TableHead>Source</TableHead>
            <TableHead className="text-right">Views</TableHead>
            <TableHead className="text-right">Likes</TableHead>
            <TableHead className="pr-5 text-right">Comments</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {clips.map((c) => (
            <TableRow key={`${c.platform}-${c.external_id}`}>
              <TableCell className="max-w-64 truncate pl-5 font-medium">
                {c.clip_id ? (
                  <Link href={`/app/clips/${c.clip_id}`} className="hover:underline">
                    {c.title || "Untitled clip"}
                  </Link>
                ) : (
                  c.title || "Deleted clip"
                )}
              </TableCell>
              <TableCell className="text-muted-foreground">
                <span className="flex items-center gap-1.5" title={`${c.source} · fetched ${formatRelative(c.fetched_at)}`}>
                  <PlatformIcon platform={c.platform} className="size-3.5" /> {PLATFORM_META[c.platform]?.label ?? c.platform}
                </span>
              </TableCell>
              <TableCell className="text-right tabular-nums">{formatCount(c.views)}</TableCell>
              <TableCell className="text-right tabular-nums">{formatCount(c.likes)}</TableCell>
              <TableCell className="pr-5 text-right tabular-nums">{formatCount(c.comments)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </SectionCard>
  );
}

export function PlatformSection({ data }: { data: AnalyticsOverview }) {
  const platforms: Platform[] = ["youtube", "instagram"];
  const tracked = platforms.reduce((n, p) => n + (data.platform[p]?.publications_tracked ?? 0), 0);
  return (
    <section aria-labelledby="platform-metrics" className="space-y-4">
      <div>
        <h2 id="platform-metrics" className="font-heading text-lg font-semibold">
          Platform performance
        </h2>
        <p className="text-sm text-muted-foreground">Real numbers reported by the platforms, totalled since publication.</p>
      </div>
      {tracked === 0 && data.internal.totals.published === 0 ? (
        <Surface>
          <EmptyState
            icon={CalendarClock}
            title="Nothing published yet"
            description="Views, likes and comments appear here once your first clip is live."
            action={
              <Link href="/app/calendar" className={cn(buttonVariants(), "rounded-xl")}>
                Open the calendar
              </Link>
            }
          />
        </Surface>
      ) : (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            {platforms.map((p) => data.platform[p] && <PlatformCard key={p} platform={p} data={data.platform[p]} />)}
          </div>
          {data.top_published_clips.length > 0 && <TopClips clips={data.top_published_clips} />}
        </>
      )}
    </section>
  );
}
