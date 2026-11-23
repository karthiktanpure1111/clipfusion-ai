"use client";

import { useMemo, useState } from "react";
import { FileText, Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useTranscript } from "@/lib/api/hooks/sources";
import { formatTimestamp } from "@/lib/format";
import { cn } from "@/lib/utils";

function highlight(text: string, q: string): React.ReactNode {
  if (!q) return text;
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return text;
  return (
    <>
      {text.slice(0, i)}
      <mark className="rounded bg-[#FCAF45]/30 px-0.5 text-foreground">{text.slice(i, i + q.length)}</mark>
      {highlight(text.slice(i + q.length), q)}
    </>
  );
}

export function TranscriptViewer({
  sourceId,
  enabled,
  currentTime,
  onSeek,
}: {
  sourceId: string;
  enabled: boolean;
  currentTime: number;
  onSeek?: (t: number) => void;
}) {
  const { data, isLoading, error } = useTranscript(sourceId, enabled);
  const [q, setQ] = useState("");
  const segments = useMemo(() => {
    const all = data?.segments ?? [];
    const query = q.trim().toLowerCase();
    return query ? all.filter((s) => s.text.toLowerCase().includes(query)) : all;
  }, [data, q]);

  if (!enabled) return <p className="text-sm text-muted-foreground">The transcript appears here once the video has been analysed.</p>;
  if (isLoading) return <div className="space-y-2">{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-10" />)}</div>;
  if (error || !data)
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <FileText className="size-4" /> No transcript available yet.
      </p>
    );

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <Search className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search transcript…" aria-label="Search transcript" className="h-9 rounded-xl pl-9" />
        </div>
        <span className="shrink-0 text-xs text-muted-foreground">
          {data.word_count.toLocaleString()} words · {data.language ?? "auto"} · {data.model}
        </span>
      </div>
      <ol className="max-h-[440px] space-y-0.5 overflow-y-auto pr-1" aria-label="Transcript segments">
        {segments.map((s) => {
          const active = currentTime >= s.start && currentTime < s.end;
          return (
            <li key={s.id}>
              <button
                type="button"
                onClick={() => onSeek?.(s.start)}
                disabled={!onSeek}
                className={cn(
                  "flex w-full gap-3 rounded-lg px-2.5 py-2 text-left text-sm transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                  active ? "bg-ig-pink/10 ring-1 ring-ig-pink/30" : "hover:bg-white/4",
                )}
              >
                <span className="w-12 shrink-0 pt-px font-mono text-xs text-muted-foreground tabular-nums">{formatTimestamp(s.start)}</span>
                <span className={cn("flex-1", active ? "text-foreground" : "text-foreground/80")}>{highlight(s.text.trim(), q.trim())}</span>
              </button>
            </li>
          );
        })}
        {!segments.length && <li className="py-6 text-center text-sm text-muted-foreground">No matches for “{q}”.</li>}
      </ol>
    </div>
  );
}
