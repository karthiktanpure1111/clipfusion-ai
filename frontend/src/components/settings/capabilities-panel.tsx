"use client";

import { CheckCircle2, CircleSlash, Server } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState, SectionCard } from "@/components/common/page";
import { useCapabilities } from "@/lib/api/hooks/sources";
import type { Capabilities } from "@/lib/api/types";
import { titleCase } from "@/lib/format";

interface Row {
  label: string;
  ok: boolean;
  detail: React.ReactNode;
}

function envList(missing: string[] | undefined) {
  if (!missing?.length) return null;
  return (
    <>
      Missing on the server:{" "}
      {missing.map((m) => (
        <code key={m} className="mr-1 rounded bg-white/8 px-1 py-px font-mono text-[11px]">
          {m}
        </code>
      ))}
    </>
  );
}

function rows(c: Capabilities): Row[] {
  const ff = c.ffmpeg ?? {};
  // libass (burned-in subtitles) is no longer used: clips are exported clean
  const ffMissing = Object.entries(ff)
    .filter(([k, v]) => k !== "libass" && !v)
    .map(([k]) => k);
  return [
    { label: "FFmpeg rendering", ok: ffMissing.length === 0, detail: ffMissing.length ? `Not available: ${ffMissing.join(", ")}` : "FFmpeg, FFprobe and H.264 encoding available" },
    {
      label: "Speech-to-text (Whisper)",
      ok: c.whisper.available,
      detail: c.whisper.available ? [c.whisper.model, c.whisper.device].filter(Boolean).join(" on ") : "Not available — videos can't be transcribed",
    },
    { label: "Face detection", ok: c.face_detectors.length > 0, detail: c.face_detectors.length ? c.face_detectors.map(titleCase).join(", ") : "No face detector installed" },
    { label: "AI language model", ok: c.llm.configured, detail: c.llm.configured ? (c.llm.model ?? "Configured") : "Not configured (optional) — free local AI ranks highlights and writes titles, descriptions and hashtags" },
    { label: "YouTube integration", ok: c.youtube_configured, detail: c.youtube_configured ? "OAuth credentials configured" : (envList(c.google_missing_env) ?? "Not configured") },
    {
      label: "Instagram integration",
      ok: c.instagram_configured,
      detail: c.instagram_configured ? `Configured${c.instagram_auth_flow ? ` (${titleCase(c.instagram_auth_flow)})` : ""}` : (envList(c.instagram_missing_env) ?? "Not configured"),
    },
  ];
}

export function CapabilitiesPanel() {
  const { data, isLoading, error, refetch } = useCapabilities();
  return (
    <SectionCard title="System capabilities" description="What this ClipFusion server can really do right now." icon={Server}>
      {error ? (
        <ErrorState error={error} onRetry={() => refetch()} />
      ) : isLoading || !data ? (
        <Skeleton className="h-48 rounded-xl" />
      ) : (
        <>
          <ul className="grid gap-2 md:grid-cols-2">
            {rows(data).map((r) => (
              <li key={r.label} className="flex gap-3 rounded-xl border border-white/7 bg-black/15 p-3">
                {r.ok ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" aria-label="Available" /> : <CircleSlash className="mt-0.5 size-4 shrink-0 text-warning" aria-label="Unavailable" />}
                <div className="min-w-0 text-sm">
                  <p className="font-medium">{r.label}</p>
                  <p className="text-xs break-words text-muted-foreground">{r.detail}</p>
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-muted-foreground">
            Storage: {data.storage_backend} · Background worker: {data.worker_mode}
          </p>
        </>
      )}
    </SectionCard>
  );
}
