"use client";

import { useState } from "react";
import { Ban, Check, Download, Eye, FileVideo, Link2, Loader2, LineChart, Search, Sparkles, Timer, UploadCloud } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RightsCheckbox } from "@/components/common/fields";
import { Segmented } from "@/components/common/segmented";
import { VideoThumb } from "@/components/common/video-thumb";
import { YouTubeIcon } from "@/components/common/brand";
import { useMe } from "@/lib/api/hooks/auth";
import { useCreateYoutubeSource, useDownloadYouTube, useYouTubeQualities } from "@/lib/api/hooks/sources";
import type { AnalysisRequest, SourceOut, YouTubeDownloadQuality, YouTubeQuality } from "@/lib/api/types";
import { formatBytes, formatCount, formatDate, formatDuration } from "@/lib/format";
import { parseYouTubeUrl, YOUTUBE_URL_HINT } from "@/lib/youtube";
import { UploadPanel } from "./upload-panel";

export const canAnalyze = (s: SourceOut) => s.has_file && ["uploaded", "ready", "failed"].includes(s.status) && !s.active_job;

export function YouTubeMetaCard({ source }: { source: SourceOut }) {
  const m = source.youtube_metadata ?? {};
  const duration = m.duration_seconds ?? source.duration;
  return (
    <div className="flex flex-col gap-4 rounded-2xl border border-white/8 bg-black/20 p-4 sm:flex-row">
      <VideoThumb src={m.thumbnail_url ?? source.thumbnail_url} alt="" className="w-full shrink-0 rounded-xl sm:w-56">
        <YouTubeIcon className="absolute top-2 left-2 size-5" />
      </VideoThumb>
      <div className="min-w-0 space-y-2">
        <p className="line-clamp-2 font-medium">{m.title || source.title}</p>
        {m.channel_title && <p className="text-sm text-muted-foreground">{m.channel_title}</p>}
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          {duration != null && (
            <span className="flex items-center gap-1"><Timer className="size-3.5" /> {formatDuration(duration)}</span>
          )}
          {m.view_count != null && (
            <span className="flex items-center gap-1" title="Public view count from the YouTube Data API">
              <Eye className="size-3.5" /> {formatCount(m.view_count)} views · YouTube Data API
            </span>
          )}
          {m.published_at && <span>Published {formatDate(m.published_at)}</span>}
        </div>
        {source.attribution && <p className="text-xs text-muted-foreground">Credit: {source.attribution.replace(/^credit:\s*/i, "")}</p>}
        {source.has_retention && (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-success/10 px-2.5 py-1 text-xs text-success ring-1 ring-success/25">
            <LineChart className="size-3.5" /> Real audience retention available (YouTube Analytics)
          </span>
        )}
      </div>
    </div>
  );
}

export function NoYouTubeDownloadNote() {
  const me = useMe();
  if (me.data?.settings.youtube_download_enabled) return null;
  return (
    <div className="flex gap-2.5 rounded-xl border border-warning/25 bg-warning/5 p-3 text-xs leading-relaxed text-muted-foreground">
      <Ban className="mt-0.5 size-4 shrink-0 text-warning" />
      <p>
        <span className="font-medium text-foreground">Downloading from YouTube links is off.</span> The link is used to read the title, channel,
        duration and thumbnail. To make clips, upload the creator&apos;s video file — or turn on &ldquo;Download from YouTube links&rdquo; in
        Settings (YouTube&apos;s Terms of Service don&apos;t allow downloading; you take that risk).
      </p>
    </div>
  );
}

type QualityOption = { value: YouTubeDownloadQuality | "480"; title: string; detail: string; height: number };

/** "Best available" first, then one option per resolution (its highest frame rate). */
function qualityOptions(list: YouTubeQuality[]): QualityOption[] {
  const perHeight = new Map<number, YouTubeQuality>();
  for (const q of list) {
    const cur = perHeight.get(q.height);
    if (!cur || q.fps > cur.fps) perHeight.set(q.height, q);
  }
  const sized = (q: YouTubeQuality) => (q.size_bytes ? ` · ≈${formatBytes(q.size_bytes)}` : "");
  const rows = [...perHeight.values()].sort((a, b) => b.height - a.height);
  const top = rows[0];
  const allowed = new Set(["2160", "1440", "1080", "720", "480"]);
  return [
    { value: "best", title: "Best available", detail: top ? `${top.label}${sized(top)}` : "Highest resolution & frame rate", height: top?.height ?? 0 },
    ...rows
      .filter((q) => allowed.has(q.quality))
      .slice(top ? 1 : 0)
      .map((q) => ({ value: q.quality as QualityOption["value"], title: q.label, detail: `${q.fps} fps${sized(q)}`, height: q.height })),
  ];
}

/** Download the YouTube video itself (opt-in in Settings) and start processing. */
function YouTubeDownloadForm({ source, analysis, blockedReason, rightsConfirmed, onDone }: {
  source: SourceOut;
  analysis?: AnalysisRequest;
  blockedReason?: string | null;
  rightsConfirmed: boolean;
  onDone: (s: SourceOut) => void;
}) {
  const me = useMe();
  const [rights, setRights] = useState(rightsConfirmed);
  const [picked, setPicked] = useState<QualityOption["value"] | null>(null);
  const qualities = useYouTubeQualities(source.id);
  const dl = useDownloadYouTube();
  const options = qualities.data ? qualityOptions(qualities.data.qualities) : [];
  const preferred = me.data?.settings.youtube_download_quality ?? "best";
  const quality = picked ?? (options.some((o) => o.value === preferred) ? preferred : "best");
  const selected = options.find((o) => o.value === quality);
  const big = (selected?.height ?? 0) >= 1440;

  return (
    <form
      className="space-y-3"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!rights || blockedReason) return;
        const s = await dl
          .mutateAsync({ id: source.id, quality, rights_confirmed: true, auto_analyze: !!analysis, ...(analysis ? { analysis } : {}) })
          .catch(() => null);
        if (s) onDone(s);
      }}
    >
      <p className="text-sm font-medium">Download quality</p>
      {qualities.isPending ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" /> Checking which qualities this video has…
        </p>
      ) : qualities.isError ? (
        <p className="text-xs text-muted-foreground">
          Couldn&apos;t list the qualities ({qualities.error.message}). The best available quality will be downloaded.
        </p>
      ) : (
        <div role="radiogroup" aria-label="Download quality" className="grid gap-2 sm:grid-cols-2">
          {options.map((o) => {
            const active = o.value === quality;
            return (
              <button
                key={o.value}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setPicked(o.value)}
                className={
                  "flex items-center gap-3 rounded-xl px-3 py-2.5 text-left ring-1 transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring " +
                  (active ? "bg-white/10 ring-white/25" : "bg-white/3 ring-white/8 hover:bg-white/6")
                }
              >
                <span className={"grid size-5 shrink-0 place-items-center rounded-full ring-1 " + (active ? "bg-primary text-primary-foreground ring-primary" : "ring-white/20")}>
                  {active && <Check className="size-3" />}
                </span>
                <span className="min-w-0">
                  <span className="flex items-center gap-1.5 text-sm font-medium">
                    {o.value === "best" && <Sparkles className="size-3.5 text-warning" />} {o.title}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">{o.detail}</span>
                </span>
              </button>
            );
          })}
        </div>
      )}
      {big && (
        <p className="text-xs text-muted-foreground">
          4K / 2K files are big, so downloading and processing take longer. The Shorts are exported at 1080×1920 either way, but a sharper source
          keeps zoomed-in framing crisp.
        </p>
      )}
      <p className="text-xs text-muted-foreground">Public videos only (not private, members-only, age-restricted or still-live streams). Download speed depends on your internet connection.</p>
      {!rightsConfirmed && <RightsCheckbox id={`rights-dl-${source.id}`} checked={rights} onChange={setRights} />}
      {blockedReason && <p className="text-xs text-[#ff5c7a]">{blockedReason}</p>}
      <Button type="submit" size="lg" className="glow-red h-10 rounded-xl" disabled={!rights || !!blockedReason || dl.isPending}>
        {dl.isPending ? <Loader2 className="animate-spin" /> : <Download />} {analysis ? "Download from YouTube & start processing" : "Download from YouTube"}
      </Button>
    </form>
  );
}

/** Give a metadata-only source its media: download it from YouTube (if switched on) or upload the creator's file. */
export function AttachMediaPanel({
  source,
  analysis,
  blockedReason,
  rightsConfirmed = false,
  onAttached,
}: {
  source: SourceOut;
  analysis?: AnalysisRequest;
  blockedReason?: string | null;
  rightsConfirmed?: boolean;
  onAttached: (s: SourceOut) => void;
}) {
  const me = useMe();
  const canDownload = !!me.data?.settings.youtube_download_enabled && !!source.youtube_video_id;
  const [choice, setHow] = useState<"download" | "file" | null>(null);
  const how = canDownload ? (choice ?? "download") : "file";
  return (
    <div className="space-y-4">
      {canDownload && (
        <Segmented
          ariaLabel="How to provide the video"
          value={how}
          onChange={setHow}
          options={[
            { value: "download" as const, label: <><Download className="size-4" /> Download from YouTube</> },
            { value: "file" as const, label: <><UploadCloud className="size-4" /> Upload the creator&apos;s file</> },
          ]}
        />
      )}
      {how === "download" ? (
        <YouTubeDownloadForm source={source} analysis={analysis} blockedReason={blockedReason} rightsConfirmed={rightsConfirmed} onDone={onAttached} />
      ) : (
        <UploadPanel compact videoSourceId={source.id} analysis={analysis} blockedReason={blockedReason} rightsConfirmed={rightsConfirmed} onComplete={onAttached} />
      )}
    </div>
  );
}

export function YouTubePanel({ analysis, blockedReason, onReady }: { analysis: AnalysisRequest; blockedReason?: string | null; onReady: (s: SourceOut) => void }) {
  const [url, setUrl] = useState("");
  const [rights, setRights] = useState(false);
  const create = useCreateYoutubeSource();
  const [source, setSource] = useState<SourceOut | null>(null);
  const videoId = parseYouTubeUrl(url);
  const showHint = url.trim() !== "" && !videoId;

  return (
    <div className="space-y-5">
      <form
        className="space-y-3"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!videoId || !rights) return;
          const created = await create.mutateAsync({ url: url.trim(), rights_confirmed: true }).catch(() => null);
          if (created) setSource(created);
        }}
      >
        <div className="flex flex-col gap-2 sm:flex-row">
          <Label htmlFor="yt-url" className="sr-only">YouTube video URL</Label>
          <div className="relative flex-1">
            <Link2 className="absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              id="yt-url"
              value={url}
              onChange={(e) => {
                setUrl(e.target.value);
                setSource(null);
              }}
              placeholder="https://www.youtube.com/watch?v=…"
              aria-invalid={showHint || undefined}
              aria-describedby="yt-url-hint"
              className="h-10 rounded-xl pl-9"
            />
          </div>
          <Button type="submit" size="lg" className="h-10 rounded-xl" disabled={!videoId || !rights || create.isPending}>
            {create.isPending ? <Loader2 className="animate-spin" /> : <Search />} Fetch details
          </Button>
        </div>
        <p id="yt-url-hint" className={showHint ? "text-xs text-[#ff5c7a]" : "text-xs text-muted-foreground"}>
          {showHint ? `That isn't a YouTube video link. ${YOUTUBE_URL_HINT}` : YOUTUBE_URL_HINT}
        </p>
        <RightsCheckbox id="rights-youtube" checked={rights} onChange={setRights} />
      </form>
      <NoYouTubeDownloadNote />
      {source && (
        <div className="space-y-5">
          <YouTubeMetaCard source={source} />
          {source.has_file ? (
            <Button size="lg" className="rounded-xl" onClick={() => onReady(source)}>
              <FileVideo /> This video already has its file — open it
            </Button>
          ) : (
            <div className="space-y-3">
              <h3 className="flex items-center gap-2 font-heading text-sm font-semibold">
                <FileVideo className="size-4" /> Get the video
              </h3>
              <AttachMediaPanel source={source} analysis={analysis} blockedReason={blockedReason} rightsConfirmed onAttached={onReady} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
