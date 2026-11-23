"use client";

import { useState, type ReactNode } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { Film, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { errorMessage, useClips, type ClipOut } from "@/lib/api/editor";
import { formatTime } from "./player";

const PAGE = 24;

/** Recent clips (GET /clips) linking to one of the clip editors. */
export function ClipEntryGrid({ section, renderMeta }: { section: "framing"; renderMeta: (clip: ClipOut) => ReactNode }) {
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const clips = useClips({ limit, q });
  const items = clips.data?.items ?? [];
  return (
    <section aria-label="Recent clips" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-heading text-base font-semibold">Recent clips</h2>
        <form
          className="relative w-full max-w-64"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            setQ(draft.trim());
          }}
        >
          <Search className="absolute top-2 left-2.5 size-4 text-muted-foreground" aria-hidden />
          <Input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Search clips, press Enter" aria-label="Search clips" className="pl-8" />
        </form>
      </div>
      {clips.isError && <p className="rounded-xl bg-destructive/10 p-3 text-sm text-destructive">Could not load clips: {errorMessage(clips.error)}</p>}
      {clips.isLoading && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} className="h-56 rounded-2xl" />
          ))}
        </div>
      )}
      {clips.isSuccess && items.length === 0 && (
        <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center">
          <Film className="mx-auto mb-2 size-8 text-muted-foreground" aria-hidden />
          <p className="text-sm font-medium">{q ? "No clips match your search" : "No clips yet"}</p>
          <p className="mt-1 text-xs text-muted-foreground">Add a video first — its clips appear here.</p>
        </div>
      )}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
        {items.map((c, i) => (
          <motion.div key={c.id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(i, 8) * 0.03 }}>
            <Link
              href={`/app/clips/${c.id}/${section}`}
              className="group block overflow-hidden rounded-2xl border border-white/[0.06] bg-card/70 transition outline-none hover:border-white/20 focus-visible:ring-2 focus-visible:ring-ring"
            >
              <div className="relative aspect-video overflow-hidden bg-black">
                {c.thumbnail_url ? (
                  // eslint-disable-next-line @next/next/no-img-element -- short-lived signed media URL
                  <img src={c.thumbnail_url} alt="" loading="lazy" className="size-full object-cover transition duration-300 group-hover:scale-105" />
                ) : (
                  <div className="grid size-full place-items-center text-muted-foreground">
                    <Film className="size-6" aria-hidden />
                  </div>
                )}
                <span className="absolute right-2 bottom-2 rounded-md bg-black/75 px-1.5 py-0.5 font-mono text-[10px]">{formatTime(c.duration)}</span>
              </div>
              <div className="space-y-1.5 p-3">
                <p className="line-clamp-1 text-sm font-semibold">{c.title || "Untitled clip"}</p>
                <p className="line-clamp-1 text-[11px] text-muted-foreground">{c.source_title}</p>
                <div className="flex flex-wrap items-center gap-1.5 pt-0.5">{renderMeta(c)}</div>
              </div>
            </Link>
          </motion.div>
        ))}
      </div>
      {clips.data && clips.data.total > items.length && (
        <div className="text-center">
          <Button variant="outline" size="sm" onClick={() => setLimit((l) => l + PAGE)} disabled={clips.isFetching}>
            Show more ({clips.data.total - items.length})
          </Button>
        </div>
      )}
    </section>
  );
}

export function MetaChip({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "warning" | "success" }) {
  const cls = tone === "warning" ? "bg-warning/15 text-warning" : tone === "success" ? "bg-success/15 text-success" : "bg-muted text-muted-foreground";
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium ${cls}`}>{children}</span>;
}
