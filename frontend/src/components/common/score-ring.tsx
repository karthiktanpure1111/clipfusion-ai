import { cn } from "@/lib/utils";
import { AI_SCORE_LABEL } from "@/lib/format";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/** Circular AI highlight score (0-100). Always labelled as an estimate. */
export function ScoreRing({
  score,
  size = 44,
  showLabel = false,
  className,
}: {
  score: number;
  size?: number;
  showLabel?: boolean;
  className?: string;
}) {
  const value = Math.max(0, Math.min(100, score ?? 0));
  const stroke = Math.max(3, size / 11);
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const gid = `score-${size}`;
  const ring = (
    <span
      className={cn("relative inline-grid shrink-0 place-items-center", className)}
      style={{ width: size, height: size }}
      role="img"
      aria-label={`${AI_SCORE_LABEL}: ${Math.round(value)} out of 100`}
    >
      <svg width={size} height={size} className="-rotate-90">
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#FCAF45" />
            <stop offset=".5" stopColor="#DD2A7B" />
            <stop offset="1" stopColor="#8134AF" />
          </linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={r} fill="rgb(10 10 14 / .7)" stroke="rgb(255 255 255 / .1)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={`url(#${gid})`}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - value / 100)}
        />
      </svg>
      <span className="absolute font-heading font-bold tabular-nums" style={{ fontSize: size * 0.32 }}>
        {Math.round(value)}
      </span>
    </span>
  );
  return (
    <span className="inline-flex items-center gap-2">
      <Tooltip>
        <TooltipTrigger render={<span tabIndex={0} className="rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring" />}>
          {ring}
        </TooltipTrigger>
        <TooltipContent className="max-w-56">
          {AI_SCORE_LABEL}. Estimated from transcript, audio and visual signals — not views or audience retention.
        </TooltipContent>
      </Tooltip>
      {showLabel && <span className="text-[11px] leading-tight text-muted-foreground">{AI_SCORE_LABEL}</span>}
    </span>
  );
}
