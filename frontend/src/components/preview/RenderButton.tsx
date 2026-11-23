"use client";

import { useState } from "react";
import { AlertTriangle, Clapperboard, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { ApiError } from "@/lib/api/client";
import { errorMessage, isJobActive, useJob, useRenderClip } from "@/lib/api/editor";
import type { FramingWarning } from "@/lib/framing/types";
import { WarningBadge } from "./WarningBadge";

/**
 * Render (POST /clips/{id}/render). Unsaved edits are saved first. A 409
 * ``framing_warnings_unacknowledged`` shows the warnings with "Fix framing" or
 * "Render anyway (I reviewed the framing)" which re-posts with ``acknowledge_warnings``.
 */
export function RenderButton({
  clipId,
  dirty,
  onSave,
  onFixFraming,
}: {
  clipId: string;
  dirty: boolean;
  onSave: () => Promise<unknown>;
  onFixFraming: () => void;
}) {
  const render = useRenderClip(clipId);
  const [warnings, setWarnings] = useState<FramingWarning[] | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const busy = render.isPending || isJobActive(job.data);

  const go = async (acknowledge: boolean) => {
    try {
      if (dirty) await onSave();
      const j = await render.mutateAsync(acknowledge);
      setWarnings(null);
      setJobId(j.id);
      toast.success("Render queued", { description: "You can keep editing; the export runs in the background." });
    } catch (e) {
      if (e instanceof ApiError && e.code === "framing_warnings_unacknowledged") {
        setWarnings(Array.isArray(e.details) ? (e.details as FramingWarning[]) : []);
      } else toast.error("Render failed to start", { description: errorMessage(e) });
    }
  };

  const status = job.data;
  return (
    <>
      <Button onClick={() => go(false)} disabled={busy} className="glow-red">
        {busy ? <Loader2 className="animate-spin" /> : <Clapperboard />}
        {status && isJobActive(status) ? `Rendering ${Math.round(status.progress * 100)}%` : "Render"}
      </Button>
      {status?.status === "failed" && <span className="sr-only">Render failed: {status.error}</span>}
      <Dialog open={warnings !== null} onOpenChange={(o) => !o && setWarnings(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="size-5 text-warning" /> Review the framing before exporting
            </DialogTitle>
            <DialogDescription>
              The crop protection found problems that could cut people or important content out of the final video.
            </DialogDescription>
          </DialogHeader>
          <ul className="max-h-72 space-y-2 overflow-y-auto">
            {(warnings ?? []).map((w, i) => (
              <li key={i} className="flex gap-2 rounded-xl bg-muted/60 p-3 text-sm">
                <WarningBadge severity={w.severity} />
                <span>{w.message}</span>
              </li>
            ))}
          </ul>
          <DialogFooter className="flex-col gap-2 sm:flex-row sm:justify-end">
            <Button
              variant="outline"
              onClick={() => {
                setWarnings(null);
                onFixFraming();
              }}
            >
              Fix framing
            </Button>
            <Button onClick={() => go(true)} disabled={render.isPending}>
              {render.isPending && <Loader2 className="animate-spin" />}
              Render anyway (I reviewed the framing)
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
