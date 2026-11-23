"use client";

import { useMemo, useState, type ReactNode } from "react";
import { CheckCircle2, Loader2, Search, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { errorMessage, useClips } from "@/lib/api/editor";
import { cn } from "@/lib/utils";

export interface ApplyResult {
  clip_id: string;
  ok: boolean;
  error?: unknown;
  warnings?: { severity: string }[];
}

/** Pick other clips (GET /clips) and apply something to them; shows per-clip results. */
export function ApplyToClipsDialog({
  open,
  onOpenChange,
  title,
  description,
  excludeId,
  confirmLabel,
  onApply,
  canApply = true,
  children,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: string;
  description: string;
  excludeId: string;
  confirmLabel: string;
  onApply: (ids: string[]) => Promise<ApplyResult[]>;
  canApply?: boolean;
  /** Extra options shown above the clip list. */
  children?: ReactNode;
}) {
  const clips = useClips({ limit: 100 });
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<ApplyResult[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const items = useMemo(
    () =>
      (clips.data?.items ?? []).filter(
        (c) => c.id !== excludeId && `${c.title ?? ""} ${c.source_title ?? ""}`.toLowerCase().includes(q.toLowerCase()),
      ),
    [clips.data, excludeId, q],
  );
  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const apply = async () => {
    setBusy(true);
    setError(null);
    try {
      setResults(await onApply([...selected]));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  const close = (o: boolean) => {
    onOpenChange(o);
    if (!o) {
      setResults(null);
      setSelected(new Set());
    }
  };
  const name = (id: string) => clips.data?.items.find((c) => c.id === id)?.title ?? id.slice(0, 8);

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {results ? (
          <ul className="max-h-80 space-y-1.5 overflow-y-auto text-sm">
            {results.map((r) => {
              const blocking = (r.warnings ?? []).filter((w) => w.severity !== "info").length;
              return (
                <li key={r.clip_id} className="flex items-center gap-2 rounded-lg bg-muted/50 px-3 py-2">
                  {r.ok ? <CheckCircle2 className="size-4 text-success" /> : <XCircle className="size-4 text-destructive" />}
                  <span className="min-w-0 flex-1 truncate">{name(r.clip_id)}</span>
                  {r.ok && blocking > 0 && <span className="text-xs text-warning">{blocking} framing warning(s) to review</span>}
                  {!r.ok && <span className="text-xs text-destructive">{typeof r.error === "string" ? r.error : errorMessage(r.error)}</span>}
                </li>
              );
            })}
          </ul>
        ) : (
          <>
            {children}
            <div className="relative">
              <Search className="absolute top-2 left-2.5 size-4 text-muted-foreground" />
              <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search clips" className="pl-8" aria-label="Search clips" />
            </div>
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>{selected.size} selected</span>
              <button type="button" className="hover:text-foreground" onClick={() => setSelected(new Set(items.map((c) => c.id)))}>
                Select all shown
              </button>
            </div>
            <ul className="max-h-72 space-y-1 overflow-y-auto" aria-label="Clips">
              {clips.isLoading && <li className="p-3 text-xs text-muted-foreground">Loading clips…</li>}
              {!clips.isLoading && items.length === 0 && <li className="p-3 text-xs text-muted-foreground">No other clips.</li>}
              {items.map((c) => (
                <li key={c.id}>
                  <label
                    className={cn(
                      "flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2 text-sm hover:bg-muted/60",
                      selected.has(c.id) && "bg-muted",
                    )}
                  >
                    <input type="checkbox" checked={selected.has(c.id)} onChange={() => toggle(c.id)} className="size-4 accent-[#DD2A7B]" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium">{c.title || "Untitled clip"}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {c.source_title} · {Math.round(c.duration)} s{c.framing_mode ? ` · ${c.framing_mode.replace("_", " ")}` : ""}
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </>
        )}
        {error && <p className="text-xs text-destructive">{error}</p>}
        <DialogFooter>
          {results ? (
            <Button onClick={() => close(false)}>Done</Button>
          ) : (
            <Button onClick={apply} disabled={!selected.size || busy || !canApply}>
              {busy && <Loader2 className="animate-spin" />}
              {confirmLabel} ({selected.size})
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
