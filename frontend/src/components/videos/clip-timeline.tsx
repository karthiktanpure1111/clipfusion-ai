"use client";

import { Clapperboard, ListOrdered } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/common/page";
import type { ClipOut } from "@/lib/api/types";
import { ClipTimelineRow } from "./clip-timeline-row";

/** Chronological order: the server's `sequence` (source start time), then start time as a fallback. */
export const bySequence = (a: ClipOut, b: ClipOut) => (a.sequence ?? 1e9) - (b.sequence ?? 1e9) || a.start_time - b.start_time;

export function ClipTimeline({ clips, loading, emptyText }: { clips: ClipOut[]; loading: boolean; emptyText: string }) {
  const sorted = [...clips].filter((c) => c.status !== "removed").sort(bySequence);
  return (
    <section aria-labelledby="clip-timeline">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 id="clip-timeline" className="flex items-center gap-2 font-heading text-lg font-semibold">
            <ListOrdered className="size-5 text-muted-foreground" /> Clips timeline
          </h2>
          <p className="text-xs text-muted-foreground">In the order they appear in the original video. Scores are AI estimates.</p>
        </div>
        {sorted.length > 0 && <span className="text-xs text-muted-foreground">{sorted.length} clip{sorted.length === 1 ? "" : "s"}</span>}
      </div>
      {loading ? (
        <div className="space-y-3">
          <Skeleton className="h-40 rounded-2xl" />
          <Skeleton className="h-40 rounded-2xl" />
        </div>
      ) : sorted.length ? (
        <ol className="space-y-3">
          {sorted.map((c) => (
            <ClipTimelineRow key={c.id} clip={c} />
          ))}
        </ol>
      ) : (
        <EmptyState icon={Clapperboard} title="No clips yet" description={emptyText} />
      )}
    </section>
  );
}
