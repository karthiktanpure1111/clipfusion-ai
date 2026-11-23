"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { AnimatePresence, motion } from "framer-motion";
import { Cpu, Plus } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState, PageHeader } from "@/components/common/page";
import { Segmented } from "@/components/common/segmented";
import { useJobs } from "@/lib/api/hooks/jobs";
import { isJobActive } from "@/lib/api/hooks/common";
import { JOB_TYPE_LABELS } from "@/lib/format";
import { cn } from "@/lib/utils";
import { JobCard } from "./job-card";

type Filter = "all" | "active" | "failed" | "finished";
const TYPE_ITEMS = [{ value: "all", label: "All job types" }, ...Object.entries(JOB_TYPE_LABELS).map(([value, label]) => ({ value, label }))];

export function JobsView() {
  const [filter, setFilter] = useState<Filter>("all");
  const [type, setType] = useState("all");
  const { data, isLoading, error, refetch } = useJobs(type === "all" ? {} : { job_type: type });

  const jobs = useMemo(() => {
    const items = [...(data?.items ?? [])];
    items.sort((a, b) => {
      const aa = isJobActive(a.status) ? 0 : 1;
      const bb = isJobActive(b.status) ? 0 : 1;
      return aa - bb || b.created_at.localeCompare(a.created_at);
    });
    return items;
  }, [data]);

  const counts = useMemo(
    () => ({
      active: jobs.filter((j) => isJobActive(j.status)).length,
      failed: jobs.filter((j) => j.status === "failed").length,
      finished: jobs.filter((j) => j.status === "succeeded" || j.status === "cancelled").length,
    }),
    [jobs],
  );

  const visible = jobs.filter((j) =>
    filter === "all"
      ? true
      : filter === "active"
        ? isJobActive(j.status)
        : filter === "failed"
          ? j.status === "failed"
          : j.status === "succeeded" || j.status === "cancelled",
  );

  return (
    <>
      <PageHeader
        title="AI Processing"
        description="Every background job — analysis, face tracking, rendering, publishing and imports — with live progress. Active jobs refresh every 2 seconds."
        actions={
          <Link href="/app/videos/new" className={cn(buttonVariants({ size: "lg" }), "rounded-xl")}>
            <Plus /> Add a video
          </Link>
        }
      />
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <Segmented
          ariaLabel="Filter jobs by status"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: "All", count: jobs.length },
            { value: "active", label: "Active", count: counts.active },
            { value: "failed", label: "Failed", count: counts.failed },
            { value: "finished", label: "Finished", count: counts.finished },
          ]}
        />
        <Select items={TYPE_ITEMS} value={type} onValueChange={(v) => setType(String(v ?? "all"))}>
          <SelectTrigger className="h-9 w-full rounded-xl sm:w-60" aria-label="Filter by job type">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {TYPE_ITEMS.map((t) => (
              <SelectItem key={t.value} value={t.value}>
                {t.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {error ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading ? (
        <div className="space-y-3">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-32 rounded-2xl" />
          ))}
        </div>
      ) : visible.length === 0 ? (
        <EmptyState
          icon={Cpu}
          title={filter === "all" ? "No jobs yet" : `No ${filter} jobs`}
          description="When you process a video, render a clip or upload, the work shows up here with live progress."
          action={
            <Link href="/app/videos/new" className={cn(buttonVariants(), "rounded-xl")}>
              Add a video
            </Link>
          }
        />
      ) : (
        <motion.div layout className="space-y-3">
          <AnimatePresence initial={false}>
            {visible.map((job) => (
              <motion.div key={job.id} layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, scale: 0.98 }}>
                <JobCard job={job} />
              </motion.div>
            ))}
          </AnimatePresence>
        </motion.div>
      )}
    </>
  );
}
