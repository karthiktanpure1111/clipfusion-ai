import { cn } from "@/lib/utils";
import type { Platform } from "@/lib/api/types";

export function LogoMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "relative grid size-8 shrink-0 place-items-center rounded-xl bg-brand-gradient shadow-[0_6px_24px_-6px_rgb(255_0_51/0.6)]",
        className,
      )}
    >
      <svg viewBox="0 0 24 24" className="size-[55%] text-white" fill="currentColor">
        <path d="M8 5.5v13a1 1 0 0 0 1.53.85l10.4-6.5a1 1 0 0 0 0-1.7L9.53 4.65A1 1 0 0 0 8 5.5Z" />
        <rect x="3" y="5" width="2.4" height="14" rx="1.2" opacity=".75" />
      </svg>
    </span>
  );
}

export function Logo({ className, collapsed = false }: { className?: string; collapsed?: boolean }) {
  return (
    <span className={cn("flex items-center gap-2.5", className)}>
      <LogoMark />
      {!collapsed && (
        <span className="font-heading text-[15px] font-bold tracking-tight">
          ClipFusion <span className="text-gradient">AI</span>
        </span>
      )}
    </span>
  );
}

export function YouTubeIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={cn("size-4", className)} aria-hidden>
      <path
        fill="#FF0033"
        d="M23.5 6.2a3 3 0 0 0-2.1-2.1C19.5 3.6 12 3.6 12 3.6s-7.5 0-9.4.5A3 3 0 0 0 .5 6.2 31 31 0 0 0 0 12a31 31 0 0 0 .5 5.8 3 3 0 0 0 2.1 2.1c1.9.5 9.4.5 9.4.5s7.5 0 9.4-.5a3 3 0 0 0 2.1-2.1A31 31 0 0 0 24 12a31 31 0 0 0-.5-5.8Z"
      />
      <path fill="#fff" d="m9.6 15.6 6.2-3.6-6.2-3.6v7.2Z" />
    </svg>
  );
}

export function InstagramIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={cn("size-4", className)} aria-hidden>
      <defs>
        <linearGradient id="cf-ig" x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="#FCAF45" />
          <stop offset=".45" stopColor="#DD2A7B" />
          <stop offset=".75" stopColor="#8134AF" />
          <stop offset="1" stopColor="#515BD4" />
        </linearGradient>
      </defs>
      <rect x="2" y="2" width="20" height="20" rx="6" fill="url(#cf-ig)" />
      <circle cx="12" cy="12" r="4.2" fill="none" stroke="#fff" strokeWidth="1.8" />
      <circle cx="17.3" cy="6.7" r="1.2" fill="#fff" />
    </svg>
  );
}

export const PLATFORM_META: Record<Platform, { label: string; product: string; color: string; chart: string }> = {
  youtube: { label: "YouTube", product: "YouTube Shorts", color: "#FF0033", chart: "#FF0033" },
  instagram: { label: "Instagram", product: "Instagram Reels", color: "#DD2A7B", chart: "#D058C8" },
};

export function PlatformIcon({ platform, className }: { platform: string; className?: string }) {
  if (platform === "youtube") return <YouTubeIcon className={className} />;
  if (platform === "instagram") return <InstagramIcon className={className} />;
  return <span className={cn("inline-block size-4 rounded-full bg-muted", className)} aria-hidden />;
}
