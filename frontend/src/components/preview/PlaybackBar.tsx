"use client";

import { Pause, Play, Repeat, Volume2, VolumeX } from "lucide-react";
import { cn } from "@/lib/utils";

import { TipButton } from "./controls";
import { formatTime, usePlayerState, type ClipPlayer } from "./player";

/** Compact transport: play/pause, time, scrubber over the clip, loop and mute. */
export function PlaybackBar({ player, className }: { player: ClipPlayer; className?: string }) {
  const s = usePlayerState(player);
  return (
    <div className={cn("flex items-center gap-2", className)}>
      <TipButton label={s.playing ? "Pause" : "Play"} shortcut="Space" size="icon-sm" onClick={() => player.toggle()} disabled={!s.ready}>
        {s.playing ? <Pause /> : <Play />}
      </TipButton>
      <span className="w-[86px] shrink-0 text-[11px] tabular-nums text-muted-foreground">
        {formatTime(s.time, true)} / {formatTime(s.duration)}
      </span>
      <input
        type="range"
        aria-label="Seek within the clip"
        min={0}
        max={Math.max(s.duration, 0.01)}
        step={0.01}
        value={Math.min(s.time, s.duration)}
        onChange={(e) => player.seek(Number(e.target.value))}
        className="h-1 min-w-0 flex-1 cursor-pointer accent-[#FF0033]"
      />
      <TipButton
        label={s.loop ? "Looping inside the clip" : "Loop off"}
        size="icon-sm"
        variant="ghost"
        aria-pressed={s.loop}
        onClick={() => player.setLoop(!s.loop)}
        className={cn(s.loop && "text-ig-pink")}
      >
        <Repeat />
      </TipButton>
      <TipButton label={s.muted ? "Unmute" : "Mute"} size="icon-sm" variant="ghost" onClick={() => player.setMuted(!s.muted)}>
        {s.muted ? <VolumeX /> : <Volume2 />}
      </TipButton>
    </div>
  );
}
