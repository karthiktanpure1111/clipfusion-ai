"use client";

/** "AI suggestion" (POST framing/suggest) and "Re-run face detection" (job polled until done). */
import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2, ScanFace, Sparkles } from "lucide-react";
import { toast } from "sonner";

import { Panel, TipButton } from "@/components/preview/controls";
import { editorKeys, errorMessage, isJobActive, useAnalyzeFraming, useJob, useSuggestFraming } from "@/lib/api/editor";
import type { ClipVisionAnalysis, FramingConfig } from "@/lib/framing/types";
import { modeTitle } from "./PresetsMenu";

export function AiAssistPanel({
  clipId,
  vision,
  onSuggestion,
}: {
  clipId: string;
  vision: ClipVisionAnalysis | null;
  onSuggestion: (config: FramingConfig) => void;
}) {
  const qc = useQueryClient();
  const suggest = useSuggestFraming(clipId);
  const analyze = useAnalyzeFraming(clipId);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const running = isJobActive(job.data);
  const status = job.data?.status;
  const error = job.data?.error;

  // Refresh the vision data once face detection finishes.
  useEffect(() => {
    if (status === "succeeded") {
      void qc.invalidateQueries({ queryKey: editorKeys.framing(clipId) });
      toast.success("Face detection finished");
    } else if (status === "failed") toast.error("Face detection failed", { description: error ?? undefined });
  }, [status, error, qc, clipId]);

  return (
    <Panel title="AI assist">
      <div className="flex flex-wrap gap-2">
        <TipButton
          label="Let the AI propose a framing for this clip (nothing is saved until you save)"
          size="sm"
          variant="secondary"
          disabled={suggest.isPending}
          onClick={async () => {
            try {
              const res = await suggest.mutateAsync();
              onSuggestion(res.config);
              toast.success(`AI suggestion: ${modeTitle(res.config.mode)}`, { description: "Review the preview and the warnings, then save." });
            } catch (e) {
              toast.error("No suggestion available", { description: errorMessage(e) });
            }
          }}
        >
          {suggest.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />} AI suggestion
        </TipButton>
        <TipButton
          label="Detect and track faces again"
          size="sm"
          variant="outline"
          disabled={analyze.isPending || running}
          onClick={async () => {
            try {
              setJobId((await analyze.mutateAsync()).id);
            } catch (e) {
              toast.error("Could not start face detection", { description: errorMessage(e) });
            }
          }}
        >
          {running || analyze.isPending ? <Loader2 className="animate-spin" /> : <ScanFace />}
          {running ? `Detecting ${Math.round((job.data?.progress ?? 0) * 100)}%` : "Re-run face detection"}
        </TipButton>
      </div>
      <p className="mt-2 text-[11px] text-muted-foreground">
        {vision ? `${vision.tracks.length} face track(s) · detector: ${vision.detector}` : "Face analysis has not run for this clip yet."}
      </p>
    </Panel>
  );
}
