"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowRight, CalendarClock, Clapperboard, Cpu, Plus, Sparkles } from "lucide-react";
import { StoragePanel } from "@/components/storage/storage-panel";
import { buttonVariants } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { PlatformIcon } from "@/components/common/brand";
import { EmptyState, ErrorState, PageHeader, SectionCard, itemVariants, listVariants } from "@/components/common/page";
import { StatusBadge } from "@/components/common/status-badge";
import { VideoThumb } from "@/components/common/video-thumb";
import { ClipCard } from "@/components/clips/clip-card";
import { JobCard } from "@/components/processing/job-card";
import { useMe, useUserTz } from "@/lib/api/hooks/auth";
import { useDashboard } from "@/lib/api/hooks/jobs";
import type { QueueItemOut } from "@/lib/api/types";
import { formatDateTime, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ClipsPerDayChart, PublishedPerDayChart } from "./charts";
import { StatCards } from "./stat-cards";

function linkBtn(variant: "ghost" | "outline" | "default" = "ghost") {
  return cn(buttonVariants({ variant, size: "sm" }), "rounded-lg");
}

function Upcoming({ items }: { items: QueueItemOut[] }) {
  const tz = useUserTz();
  if (!items.length)
    return (
      <EmptyState
        icon={CalendarClock}
        title="Nothing scheduled"
        description="Upload clips from a video's page, or set up a schedule to fill the publishing queue."
        action={<Link href="/app/calendar" className={linkBtn("outline")}>Open calendar</Link>}
        className="py-8"
      />
    );
  return (
    <ul className="divide-y divide-white/6">
      {items.map((q) => (
        <li key={q.id} className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
          <VideoThumb src={q.thumbnail_url} alt="" vertical className="w-10 shrink-0 rounded-lg" />
          <div className="min-w-0 flex-1">
            <Link href={`/app/clips/${q.clip_id}`} className="line-clamp-1 text-sm font-medium hover:underline">
              {q.clip_title || "Untitled clip"}
            </Link>
            <div className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
              <PlatformIcon platform={q.platform} className="size-3.5" />
              <span className="truncate">{q.account_name}</span>
            </div>
          </div>
          <div className="text-right">
            <div className="text-xs font-medium tabular-nums">{formatDateTime(q.scheduled_at, tz)}</div>
            <div className="mt-1 flex justify-end">
              <StatusBadge status={q.status} />
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function DashboardView() {
  const { data, isLoading, error, refetch } = useDashboard();
  const { data: me } = useMe();

  return (
    <>
      <PageHeader
        eyebrow="Overview"
        title={me?.full_name ? `Welcome back, ${me.full_name}` : "Dashboard"}
        description="Your clipping pipeline at a glance — every number below comes straight from your workspace."
        actions={
          <>
            <Link href="/app/clips" className={cn(buttonVariants({ variant: "outline", size: "lg" }), "rounded-xl")}>
              <Clapperboard /> Generated clips
            </Link>
            <Link href="/app/videos/new" className={cn(buttonVariants({ size: "lg" }), "glow-red rounded-xl")}>
              <Plus /> Add video
            </Link>
          </>
        }
      />
      {error ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !data ? (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {Array.from({ length: 8 }, (_, i) => (
              <Skeleton key={i} className="h-[124px] rounded-2xl" />
            ))}
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Skeleton className="h-80 rounded-2xl" />
            <Skeleton className="h-80 rounded-2xl" />
          </div>
        </div>
      ) : (
        <div className="space-y-6">
          <StatCards data={data} />
          <div className="grid gap-4 lg:grid-cols-2">
            <ClipsPerDayChart data={data.clips_per_day} />
            <PublishedPerDayChart data={data.published_per_day} />
          </div>
          <div className="grid gap-4 xl:grid-cols-[1.7fr_1fr]">
            <SectionCard
              title="Recent clips"
              description="Newest AI-selected moments"
              icon={Sparkles}
              actions={
                <Link href="/app/clips" className={linkBtn()}>
                  View all <ArrowRight />
                </Link>
              }
            >
              {data.recent_clips.length ? (
                <motion.div variants={listVariants} initial="hidden" animate="show" className="grid gap-4 sm:grid-cols-2 2xl:grid-cols-3">
                  {data.recent_clips.slice(0, 6).map((c) => (
                    <motion.div key={c.id} variants={itemVariants}>
                      <ClipCard clip={c} />
                    </motion.div>
                  ))}
                </motion.div>
              ) : (
                <EmptyState
                  icon={Clapperboard}
                  title="No clips yet"
                  description="Add a video — ClipFusion processes it automatically and cuts the best moments into vertical clips."
                  action={<Link href="/app/videos/new" className={cn(buttonVariants(), "rounded-xl")}>Add your first video</Link>}
                />
              )}
            </SectionCard>
            <div className="space-y-4">
              <SectionCard
                title="Active jobs"
                description={data.active_jobs.length ? `${data.active_jobs.length} running` : "Idle"}
                icon={Cpu}
                actions={<Link href="/app/processing" className={linkBtn()}>Details <ArrowRight /></Link>}
              >
                {data.active_jobs.length ? (
                  <div className="space-y-3">
                    {data.active_jobs.slice(0, 4).map((j) => (
                      <JobCard key={j.id} job={j} compact />
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted-foreground">No background work right now.</p>
                )}
              </SectionCard>
              <StoragePanel compact />
              <SectionCard
                title="Upcoming publications"
                description={data.next_publish_at ? `Next ${formatRelative(data.next_publish_at)}` : undefined}
                icon={CalendarClock}
                actions={<Link href="/app/calendar" className={linkBtn()}>Calendar <ArrowRight /></Link>}
              >
                <Upcoming items={data.upcoming.slice(0, 6)} />
              </SectionCard>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
