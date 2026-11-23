"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState, PageHeader, Surface, itemVariants, listVariants } from "@/components/common/page";
import { Segmented } from "@/components/common/segmented";
import { useAnalytics, useRefreshAnalytics } from "@/lib/api/hooks/settings";
import type { AnalyticsOverview } from "@/lib/api/types";
import { formatCount } from "@/lib/format";
import { ClipsChart, PublishingChart, ScoreHistogram } from "./pipeline-charts";
import { PlatformSection } from "./platform-section";

function Kpis({ data }: { data: AnalyticsOverview }) {
  const t = data.internal.totals;
  const items: { label: string; value: string; hint: string }[] = [
    { label: "Videos", value: formatCount(t.videos), hint: "all time" },
    { label: "Clips generated", value: formatCount(t.clips_generated), hint: "all time" },
    { label: "Scheduled", value: formatCount(t.scheduled), hint: "right now" },
    { label: "Published", value: formatCount(t.published), hint: "all time" },
    { label: "Failed", value: formatCount(t.failed), hint: "right now" },
  ];
  return (
    <motion.ul variants={listVariants} initial="hidden" animate="show" className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
      {items.map((i) => (
        <motion.li key={i.label} variants={itemVariants}>
          <Surface className="h-full p-4">
            <p className="text-xs text-muted-foreground">{i.label}</p>
            <p className="mt-1.5 font-heading text-2xl font-bold tabular-nums">{i.value}</p>
            <p className="mt-0.5 text-[11px] text-muted-foreground">{i.hint}</p>
          </Surface>
        </motion.li>
      ))}
    </motion.ul>
  );
}

export function AnalyticsView() {
  const [days, setDays] = useState(30);
  const { data, isLoading, error, refetch } = useAnalytics(days);
  const refresh = useRefreshAnalytics();

  return (
    <>
      <PageHeader
        title="Analytics"
        description="Your pipeline counts, real platform statistics and AI score estimates, kept clearly apart."
        actions={
          <>
            <Segmented
              ariaLabel="Period"
              value={days}
              onChange={setDays}
              options={[
                { value: 7, label: "7 days" },
                { value: 30, label: "30 days" },
                { value: 90, label: "90 days" },
              ]}
            />
            <Button
              variant="outline"
              size="lg"
              className="rounded-xl"
              disabled={refresh.isPending}
              onClick={() =>
                refresh.mutate(undefined, {
                  onSuccess: (r) => toast.info(r.created ? "Fetching the latest platform statistics…" : "A refresh is already running."),
                })
              }
            >
              {refresh.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} Refresh
            </Button>
          </>
        }
      />
      {error ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !data ? (
        <div className="space-y-6">
          <Skeleton className="h-28 rounded-2xl" />
          <div className="grid gap-4 lg:grid-cols-2">
            <Skeleton className="h-80 rounded-2xl" />
            <Skeleton className="h-80 rounded-2xl" />
          </div>
        </div>
      ) : (
        <div className="space-y-8">
          <section aria-labelledby="pipeline" className="space-y-4">
            <div>
              <h2 id="pipeline" className="font-heading text-lg font-semibold">
                Your pipeline
              </h2>
              <p className="text-sm text-muted-foreground">
                {data.internal.source} · charts cover {data.period.start} to {data.period.end} ({data.period.timezone})
              </p>
            </div>
            <Kpis data={data} />
            <div className="grid gap-4 lg:grid-cols-2">
              <ClipsChart series={data.internal.series} />
              <PublishingChart series={data.internal.series} />
            </div>
          </section>
          <PlatformSection data={data} />
          <ScoreHistogram data={data.ai_highlight_scores} />
        </div>
      )}
    </>
  );
}
