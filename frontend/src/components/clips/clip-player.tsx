"use client";

import { useEffect, useRef, useState } from "react";
import { Film, MonitorPlay, Smartphone } from "lucide-react";
import { Segmented } from "@/components/common/segmented";
import { VideoThumb } from "@/components/common/video-thumb";
import { useStableUrl } from "@/components/common/use-stable-url";
import type { ClipDetail } from "@/lib/api/types";
import { formatTimestamp } from "@/lib/format";

/** Video that pauses itself when the route is hidden by <Activity> or unmounted. */
function ManagedVideo(props: React.ComponentProps<"video"> & { videoRef?: React.RefObject<HTMLVideoElement | null> }) {
  const { videoRef, ...rest } = props;
  const local = useRef<HTMLVideoElement>(null);
  const ref = videoRef ?? local;
  useEffect(() => {
    const v = ref.current;
    return () => v?.pause();
  }, [ref]);
  return <video ref={ref} {...rest} />;
}

export function ClipPlayer({ clip }: { clip: ClipDetail }) {
  // the proxy keeps its file name when it is rebuilt for a new range; renders get a new file each time
  const proxy = useStableUrl(clip.proxy_url, `${clip.proxy_start}-${clip.start_time}-${clip.end_time}`);
  const output = useStableUrl(clip.output_url, clip.rendered_at ?? "");
  const poster = useStableUrl(clip.thumbnail_url);
  const hasRender = !!output.url;
  const [tab, setTab] = useState<"source" | "rendered">(hasRender ? "rendered" : "source");
  const start = Math.max(0, clip.start_time - (clip.proxy_start ?? 0));
  const end = start + clip.duration;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Segmented
          ariaLabel="Preview"
          size="sm"
          value={tab}
          onChange={setTab}
          options={[
            { value: "source", label: <><MonitorPlay className="size-3.5" /> Source preview</> },
            { value: "rendered", label: <><Smartphone className="size-3.5" /> Rendered 9:16</> },
          ]}
        />
        <span className="text-xs text-muted-foreground tabular-nums">
          {formatTimestamp(clip.start_time, true)} – {formatTimestamp(clip.end_time, true)}
        </span>
      </div>
      <div className="grid min-h-64 place-items-center overflow-hidden rounded-2xl bg-black/60 ring-1 ring-white/8">
        {tab === "source" ? (
          proxy.url ? (
            <ManagedVideo
              key={proxy.url}
              src={proxy.url}
              poster={poster.url}
              onError={proxy.renew}
              controls
              playsInline
              preload="metadata"
              onLoadedMetadata={(e) => (e.currentTarget.currentTime = start)}
              onTimeUpdate={(e) => {
                const v = e.currentTarget;
                if (v.currentTime >= end && !v.paused) {
                  v.pause();
                  v.currentTime = start;
                }
              }}
              className="aspect-video w-full bg-black"
              aria-label="Source preview of the clip range"
            />
          ) : (
            <VideoThumb src={clip.thumbnail_url} alt="" className="w-full">
              <p className="absolute inset-x-0 bottom-0 bg-black/70 p-3 text-center text-xs text-white/80">
                The preview proxy is still being generated (face tracking & framing job).
              </p>
            </VideoThumb>
          )
        ) : hasRender ? (
          <ManagedVideo
            key={output.url}
            src={output.url}
            onError={output.renew}
            controls
            playsInline
            preload="metadata"
            className="aspect-[9/16] max-h-[70vh] bg-black"
            aria-label="Rendered vertical clip"
          />
        ) : (
          <div className="flex aspect-[9/16] max-h-[60vh] flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground">
            <Film className="size-8 opacity-40" />
            {clip.render_status === "rendering" || clip.render_status === "queued"
              ? "Rendering… the 9:16 export will appear here."
              : "Not rendered yet. Review the framing, then render the 9:16 export."}
          </div>
        )}
      </div>
      {tab === "rendered" && hasRender && !clip.render_is_current && (
        <p className="text-xs text-warning">This render is outdated — the trim or framing changed since it was made. Render again to update it.</p>
      )}
    </div>
  );
}
