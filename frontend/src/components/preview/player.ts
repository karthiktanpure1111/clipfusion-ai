"use client";

/**
 * ClipPlayer — one hidden <video> playing the clip proxy, shared by every canvas that shows
 * the clip (original panel, 9:16 output preview, thumbnails). Times are clip-relative; the
 * proxy time of clip time t is ``t + clip_start - proxy_start``. Playback loops inside the
 * clip range. The rAF loop only runs while playing and ``detach()`` (effect cleanup, also
 * when the route is hidden by <Activity>) pauses the video and stops the loop.
 */
import { useEffect, useState, useSyncExternalStore } from "react";

import { isActivatable, isTypingTarget } from "./useEditorGuards";

export interface PlayerSource {
  url: string | null;
  clipStart: number;
  clipEnd: number;
  proxyStart: number;
}

export interface PlayerSnapshot {
  time: number;
  duration: number;
  playing: boolean;
  ready: boolean;
  loop: boolean;
  muted: boolean;
  error: boolean;
}

const SERVER_SNAPSHOT: PlayerSnapshot = { time: 0, duration: 0, playing: false, ready: false, loop: true, muted: false, error: false };

export class ClipPlayer {
  video: HTMLVideoElement | null = null;
  private source: PlayerSource = { url: null, clipStart: 0, clipEnd: 0, proxyStart: 0 };
  private raf = 0;
  private lastTime = 0;
  private frameListeners = new Set<() => void>();
  private stateListeners = new Set<() => void>();
  private snap: PlayerSnapshot = SERVER_SNAPSHOT;
  private loop = true;
  private muted = false;
  private error = false;
  private cleanup: (() => void) | null = null;

  get duration(): number {
    return Math.max(0, this.source.clipEnd - this.source.clipStart);
  }

  private get offset(): number {
    return this.source.clipStart - this.source.proxyStart;
  }

  /** Current clip-relative time (precise, read it inside draw callbacks). */
  get time(): number {
    const v = this.video;
    if (!v || v.readyState < 1) return this.lastTime;
    return Math.min(Math.max(v.currentTime - this.offset, 0), this.duration);
  }

  /** True when the current video frame can be drawn. */
  get frameReady(): boolean {
    return !!this.video && this.video.readyState >= 2 && this.video.videoWidth > 0;
  }

  attach(source: PlayerSource): void {
    const urlChanged = source.url !== this.source.url;
    // Signed URLs are renewed on refetch; keep a working element instead of reloading it.
    const keepUrl = urlChanged && !!this.video && !this.error && !!this.source.url;
    this.source = keepUrl ? { ...source, url: this.source.url } : source;
    if (!source.url) {
      this.emitState();
      return;
    }
    if (!this.video) {
      const v = document.createElement("video");
      v.playsInline = true;
      v.preload = "auto";
      v.muted = this.muted;
      const onMeta = () => {
        this.error = false;
        this.seek(this.lastTime);
      };
      const onSeeked = () => this.emitFrame();
      const onPlay = () => this.startLoop();
      const onPause = () => {
        this.stopLoop();
        this.emitState();
      };
      const onErr = () => {
        this.error = true;
        this.emitState();
      };
      const onData = () => this.emitFrame();
      v.addEventListener("loadedmetadata", onMeta);
      v.addEventListener("seeked", onSeeked);
      v.addEventListener("loadeddata", onData);
      v.addEventListener("play", onPlay);
      v.addEventListener("pause", onPause);
      v.addEventListener("error", onErr);
      this.cleanup = () => {
        v.removeEventListener("loadedmetadata", onMeta);
        v.removeEventListener("seeked", onSeeked);
        v.removeEventListener("loadeddata", onData);
        v.removeEventListener("play", onPlay);
        v.removeEventListener("pause", onPause);
        v.removeEventListener("error", onErr);
      };
      this.video = v;
    }
    if (!keepUrl && this.video.getAttribute("src") !== this.source.url) this.video.src = this.source.url!;
    this.emitState();
  }

  /** Pause, stop the loop and release the media element. */
  detach(): void {
    this.lastTime = this.time;
    this.stopLoop();
    const v = this.video;
    if (v) {
      v.pause();
      this.cleanup?.();
      v.removeAttribute("src");
      v.load();
    }
    this.video = null;
    this.cleanup = null;
    this.emitState();
  }

  seek(t: number): void {
    const clamped = Math.min(Math.max(t, 0), Math.max(0, this.duration - 1e-3));
    this.lastTime = clamped;
    const v = this.video;
    if (v && v.readyState >= 1) v.currentTime = clamped + this.offset;
    this.emitFrame();
  }

  play(): void {
    const v = this.video;
    if (!v) return;
    if (this.time >= this.duration - 0.05) this.seek(0);
    void v.play().catch(() => this.emitState());
  }

  pause(): void {
    this.video?.pause();
  }

  toggle(): void {
    if (this.video && !this.video.paused) this.pause();
    else this.play();
  }

  setLoop(loop: boolean): void {
    this.loop = loop;
    this.emitState();
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    if (this.video) this.video.muted = muted;
    this.emitState();
  }

  private startLoop(): void {
    this.stopLoop();
    const tick = () => {
      const v = this.video;
      if (!v || v.paused) return;
      if (v.currentTime - this.offset >= this.duration || v.ended) {
        if (this.loop) this.seek(0);
        else {
          v.pause();
          this.seek(this.duration);
        }
      }
      this.emitFrame();
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
    this.emitState();
  }

  private stopLoop(): void {
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  /** Subscribe to every displayed frame (draw callbacks). */
  onFrame(fn: () => void): () => void {
    this.frameListeners.add(fn);
    return () => {
      this.frameListeners.delete(fn);
    };
  }

  private emitFrame(): void {
    this.frameListeners.forEach((fn) => fn());
    this.emitState();
  }

  private emitState(): void {
    const v = this.video;
    const playing = !!v && !v.paused;
    // Quantise the time while playing so React consumers re-render at ~15 Hz, not 60.
    const time = playing ? Math.round(this.time * 15) / 15 : this.time;
    const next: PlayerSnapshot = {
      time,
      duration: this.duration,
      playing,
      ready: !!v && v.readyState >= 1,
      loop: this.loop,
      muted: this.muted,
      error: this.error,
    };
    const s = this.snap;
    if (s.time === next.time && s.playing === next.playing && s.ready === next.ready && s.loop === next.loop && s.muted === next.muted && s.error === next.error && s.duration === next.duration) return;
    this.snap = next;
    this.stateListeners.forEach((fn) => fn());
  }

  subscribe = (fn: () => void): (() => void) => {
    this.stateListeners.add(fn);
    return () => {
      this.stateListeners.delete(fn);
    };
  };

  getSnapshot = (): PlayerSnapshot => this.snap;
}

/** Create a player bound to ``source`` for the lifetime of the component. */
export function useClipPlayer(source: PlayerSource): ClipPlayer {
  const [player] = useState(() => new ClipPlayer());
  const { url, clipStart, clipEnd, proxyStart } = source;
  useEffect(() => {
    player.attach({ url, clipStart, clipEnd, proxyStart });
  }, [player, url, clipStart, clipEnd, proxyStart]);
  useEffect(() => () => player.detach(), [player]);
  return player;
}

export function usePlayerState(player: ClipPlayer): PlayerSnapshot {
  return useSyncExternalStore(player.subscribe, player.getSnapshot, () => SERVER_SNAPSHOT);
}

/** Space = play/pause, Shift+Left/Right = -/+ 1 s, "," / "." = -/+ 1 frame (1/30 s). */
export function usePlayerShortcuts(player: ClipPlayer): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.code === "Space" && !isActivatable(e.target)) {
        e.preventDefault();
        player.toggle();
      } else if (e.shiftKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
        e.preventDefault();
        player.seek(player.time + (e.key === "ArrowLeft" ? -1 : 1));
      } else if (e.key === "," || e.key === ".") {
        player.pause();
        player.seek(player.time + (e.key === "," ? -1 / 30 : 1 / 30));
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [player]);
}

export function formatTime(t: number, precise = false): string {
  const s = Math.max(0, t);
  const m = Math.floor(s / 60);
  const sec = s - m * 60;
  return precise ? `${m}:${sec.toFixed(2).padStart(5, "0")}` : `${m}:${Math.floor(sec).toString().padStart(2, "0")}`;
}
