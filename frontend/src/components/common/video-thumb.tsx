"use client";

import { useState } from "react";
import { Film } from "lucide-react";
import { cn } from "@/lib/utils";
import { useStableUrl } from "./use-stable-url";

/**
 * Thumbnail with graceful fallback (signed URLs may be null or expired). Vertical renders are
 * letterboxed over a blurred copy instead of being cropped, so faces stay visible.
 */
export function VideoThumb({
  src: latestSrc,
  alt,
  className,
  children,
  vertical = false,
}: {
  src: string | null | undefined;
  alt: string;
  className?: string;
  children?: React.ReactNode;
  vertical?: boolean;
}) {
  const { url: src, renew } = useStableUrl(latestSrc);
  const [broken, setBroken] = useState<string | null>(null);
  const [portrait, setPortrait] = useState(false);
  const letterbox = portrait && !vertical;
  return (
    <div
      className={cn(
        "relative overflow-hidden bg-[radial-gradient(120%_120%_at_0%_0%,rgb(129_52_175/0.25),transparent_55%),radial-gradient(120%_120%_at_100%_100%,rgb(255_0_51/0.18),transparent_55%)] bg-[#121218]",
        vertical ? "aspect-[9/16]" : "aspect-video",
        className,
      )}
    >
      {src && broken !== src ? (
        <>
          {letterbox && (
            // eslint-disable-next-line @next/next/no-img-element -- decorative blurred backdrop
            <img src={src} alt="" aria-hidden className="absolute inset-0 size-full scale-110 object-cover opacity-50 blur-xl" />
          )}
          {/* eslint-disable-next-line @next/next/no-img-element -- signed media URLs from the API, not optimisable */}
          <img
            src={src}
            alt={alt}
            loading="lazy"
            onLoad={(e) => setPortrait(e.currentTarget.naturalHeight > e.currentTarget.naturalWidth)}
            onError={() => {
              if (!renew()) setBroken(src);
            }}
            className={cn("absolute inset-0 size-full", letterbox ? "object-contain" : "object-cover")}
          />
        </>
      ) : (
        <div className="absolute inset-0 grid place-items-center">
          <Film className="size-7 text-white/25" aria-hidden />
        </div>
      )}
      {children}
    </div>
  );
}

export function DurationPill({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={cn("rounded-md bg-black/70 px-1.5 py-0.5 text-[11px] font-medium text-white tabular-nums backdrop-blur", className)}>
      {children}
    </span>
  );
}
