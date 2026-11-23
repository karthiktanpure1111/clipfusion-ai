"use client";

import { useDeferredValue, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import { Clapperboard, Film, Loader2, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { CardGridSkeleton, EmptyState, ErrorState, PageHeader, itemVariants, listVariants } from "@/components/common/page";
import { useBulkClips, useClipPages } from "@/lib/api/hooks/clips";
import type { BulkAction } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { ClipCard } from "./clip-card";
import { ClipFilters, type ClipFilterState } from "./clip-filters";

/** Re-created when a link brings new filters (hidden routes keep their state otherwise). */
export function ClipsView() {
  const params = useSearchParams();
  return <ClipsBrowser key={params.toString()} params={params} />;
}

function ClipsBrowser({ params }: { params: { get: (name: string) => string | null } }) {
  const [f, setF] = useState<ClipFilterState>(() => ({
    q: "",
    source: params.get("video_source_id") ?? "all",
    status: params.get("status") ?? "all",
    render: params.get("render_status") ?? "all",
    minScore: 0,
    sort: params.get("video_source_id") ? "sequence" : "score",
    view: "grid",
  }));
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmRemove, setConfirmRemove] = useState(false);
  const q = useDeferredValue(f.q.trim());
  const minScore = useDeferredValue(f.minScore);
  const bulk = useBulkClips();

  const { data, isLoading, error, refetch, isFetching, hasNextPage, fetchNextPage, isFetchingNextPage } = useClipPages({
    q: q || undefined,
    video_source_id: f.source === "all" ? undefined : f.source,
    status: f.status === "all" ? undefined : f.status,
    render_status: f.render === "all" ? undefined : f.render,
    min_score: minScore || undefined,
    sort: f.sort,
  });
  const items = data?.pages.flatMap((p) => p.items) ?? [];
  const total = data?.pages.at(-1)?.total ?? 0;
  const update = (patch: Partial<ClipFilterState>) => setF((cur) => ({ ...cur, ...patch }));
  const toggle = (id: string, on: boolean) =>
    setSelected((s) => {
      const n = new Set(s);
      if (on) n.add(id);
      else n.delete(id);
      return n;
    });
  const allSelected = items.length > 0 && items.every((c) => selected.has(c.id));

  const run = async (action: BulkAction) => {
    const ids = [...selected];
    const res = await bulk.mutateAsync({ ids, action }).catch(() => null);
    if (!res) return; // MutationCache already showed the error
    const failed = res.results.filter((r) => !r.ok);
    const ok = res.results.length - failed.length;
    if (failed.length) {
      const framing = failed.some((r) => r.error?.code === "framing_warnings_unacknowledged");
      toast.warning(`${ok} done, ${failed.length} failed`, {
        description: framing
          ? "Some clips have framing warnings — open them, review the framing and render individually."
          : failed[0]?.error?.message,
      });
    } else {
      toast.success(`${ok} clip${ok === 1 ? "" : "s"} ${action === "remove" ? "removed" : "queued for rendering"}`);
    }
    setSelected(new Set(failed.map((r) => r.id)));
    setConfirmRemove(false);
  };

  const filtered = !!q || f.source !== "all" || f.status !== "all" || f.render !== "all" || f.minScore > 0;

  return (
    <>
      <PageHeader
        title="Generated Clips"
        description="Every clip the AI cut from your videos. Hover to preview, select several for bulk actions, open one to trim, frame, edit its title and hashtags, and upload."
        actions={
          <Link href="/app/videos/new" className={cn(buttonVariants({ variant: "outline", size: "lg" }), "rounded-xl")}>
            <Film /> Add another video
          </Link>
        }
      />
      <ClipFilters value={f} onChange={update} />

      <div className="mt-4 mb-3 flex items-center justify-between gap-3 text-sm text-muted-foreground">
        <label className="flex items-center gap-2">
          <Checkbox
            checked={allSelected}
            disabled={!items.length}
            onCheckedChange={(v) => setSelected(v ? new Set(items.map((c) => c.id)) : new Set())}
            aria-label="Select all visible clips"
          />
          {data ? `${total} clip${total === 1 ? "" : "s"}` : "Clips"}
          {isFetching && !isLoading && <Loader2 className="size-3.5 animate-spin" />}
        </label>
        <span className="hidden text-xs sm:block">Scores are AI highlight estimates, not views or retention.</span>
      </div>

      {error ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading ? (
        <CardGridSkeleton count={8} className="xl:grid-cols-4" />
      ) : !items.length ? (
        <EmptyState
          icon={Clapperboard}
          title={filtered ? "No clips match these filters" : "No clips yet"}
          description={filtered ? "Loosen the filters or lower the minimum score." : "Add a video and the AI will cut its best moments into clips."}
          action={
            filtered ? (
              <Button variant="outline" onClick={() => update({ q: "", source: "all", status: "all", render: "all", minScore: 0 })}>
                Clear filters
              </Button>
            ) : (
              <Link href="/app/videos/new" className={cn(buttonVariants(), "rounded-xl")}>
                Add a video
              </Link>
            )
          }
        />
      ) : (
        <>
          <motion.div
            key={f.view}
            variants={listVariants}
            initial="hidden"
            animate="show"
            className={f.view === "grid" ? "grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4" : "flex flex-col gap-3"}
          >
            {items.map((c) => (
              <motion.div key={c.id} variants={itemVariants}>
                <ClipCard clip={c} view={f.view} selected={selected.has(c.id)} onSelectChange={(v) => toggle(c.id, v)} />
              </motion.div>
            ))}
          </motion.div>
          {hasNextPage && (
            <div className="mt-6 flex justify-center">
              <Button variant="outline" onClick={() => fetchNextPage()} disabled={isFetchingNextPage}>
                {isFetchingNextPage && <Loader2 className="animate-spin" />} Load more ({total - items.length} more)
              </Button>
            </div>
          )}
        </>
      )}

      {selected.size > 0 && <div className="h-24" aria-hidden />}
      <AnimatePresence>
        {selected.size > 0 && (
          <motion.div
            initial={{ y: 80, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 80, opacity: 0 }}
            role="toolbar"
            aria-label="Bulk actions"
            className="fixed inset-x-3 bottom-4 z-40 mx-auto flex max-w-2xl flex-wrap items-center gap-2 rounded-2xl border border-white/12 bg-[#1b1b22] p-2.5 shadow-2xl sm:inset-x-6"
          >
            <span className="px-2 text-sm font-medium">{selected.size} selected</span>
            <div className="ml-auto flex flex-wrap gap-1.5">
              <Button size="sm" variant="outline" onClick={() => run("render")} disabled={bulk.isPending}>
                <Film /> Render
              </Button>
              <Button size="sm" variant="destructive" onClick={() => setConfirmRemove(true)} disabled={bulk.isPending}>
                <Trash2 /> Remove
              </Button>
              <Button size="icon-sm" variant="ghost" onClick={() => setSelected(new Set())} aria-label="Clear selection">
                <X />
              </Button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <ConfirmDialog
        open={confirmRemove}
        onOpenChange={setConfirmRemove}
        title={`Remove ${selected.size} clip${selected.size === 1 ? "" : "s"}?`}
        description="Deletes only these clips' files from this computer (rendered MP4, preview proxy, thumbnail and temporary files). A small record of each stays — title, timestamps and upload history. Clips that are uploading right now can't be removed until their upload finishes. The original videos and anything already on YouTube are not touched."
        confirmLabel="Remove"
        destructive
        pending={bulk.isPending}
        onConfirm={() => run("remove")}
      />
    </>
  );
}
