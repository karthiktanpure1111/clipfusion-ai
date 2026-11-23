"use client";

import { useEffect, useState } from "react";
import { Loader2, ScanEye } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { errorMessage, fetchPreviewFrame } from "@/lib/api/editor";
import type { FramingConfig } from "@/lib/framing/types";
import { formatTime, type ClipPlayer } from "./player";

/**
 * "Exact export frame": POST /clips/{id}/preview-frame renders the current frame with the
 * real export renderer (current unsaved framing included) for pixel-exact checks.
 */
export function ExactFrameButton({
  clipId,
  player,
  getPayload,
  size = "sm",
}: {
  clipId: string;
  player: ClipPlayer;
  getPayload: () => { framing?: FramingConfig };
  size?: "sm" | "default";
}) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<{ url: string | null; t: number; loading: boolean; error: string | null }>({
    url: null,
    t: 0,
    loading: false,
    error: null,
  });

  useEffect(() => {
    const url = state.url;
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [state.url]);

  const run = async () => {
    player.pause();
    const t = player.time;
    setOpen(true);
    setState({ url: null, t, loading: true, error: null });
    try {
      const blob = await fetchPreviewFrame(clipId, { t, ...getPayload() });
      setState({ url: URL.createObjectURL(blob), t, loading: false, error: null });
    } catch (e) {
      setState({ url: null, t, loading: false, error: errorMessage(e) });
    }
  };

  return (
    <>
      <Button variant="outline" size={size} onClick={run}>
        <ScanEye /> Exact export frame
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Exact export frame</DialogTitle>
            <DialogDescription>
              Rendered by the real export renderer at {formatTime(state.t, true)} with your current (unsaved) settings. Compare it
              with the live preview.
            </DialogDescription>
          </DialogHeader>
          <div className="checkerboard relative mx-auto aspect-[9/16] w-full max-w-[300px] overflow-hidden rounded-xl ring-1 ring-white/10">
            {state.loading && (
              <div className="absolute inset-0 grid place-items-center text-xs text-muted-foreground">
                <Loader2 className="size-5 animate-spin" />
              </div>
            )}
            {state.url && (
              // eslint-disable-next-line @next/next/no-img-element -- blob URL of a server-rendered JPEG
              <img src={state.url} alt={`Export renderer frame at ${formatTime(state.t, true)}`} className="size-full object-contain" />
            )}
            {state.error && <p className="absolute inset-4 grid place-items-center text-center text-xs text-destructive">{state.error}</p>}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
