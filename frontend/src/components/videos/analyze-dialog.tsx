"use client";

import { useState } from "react";
import { Bot, Info, Loader2, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { RightsCheckbox } from "@/components/common/fields";
import { useMe } from "@/lib/api/hooks/auth";
import { useAnalyzeSource, useCapabilities } from "@/lib/api/hooks/sources";
import type { SourceOut, UserSettings } from "@/lib/api/types";
import { cn } from "@/lib/utils";
import { analysisErrors, defaultAnalysis, ProcessingOptions } from "./processing-options";

function AnalyzeForm({ source, settings, onDone }: { source: SourceOut; settings?: UserSettings; onDone: () => void }) {
  const caps = useCapabilities();
  const analyze = useAnalyzeSource();
  const llmReady = caps.data?.llm.configured ?? false;
  const [opts, setOpts] = useState(() => ({ ...defaultAnalysis(settings), ...(source.processing_mode ? { mode: source.processing_mode } : {}) }));
  const [language, setLanguage] = useState("");
  const [useLlm, setUseLlm] = useState(settings?.default_use_llm ?? false);
  const [rights, setRights] = useState(source.rights_confirmed);
  const errors = analysisErrors(opts);

  const submit = async () => {
    const job = await analyze
      .mutateAsync({
        id: source.id,
        body: { ...opts, language: language.trim() || null, use_llm: llmReady && useLlm, rights_confirmed: true },
      })
      .catch(() => null);
    if (!job) return; // MutationCache already showed the error
    toast.success("Processing started");
    onDone();
  };

  return (
    <div className="grid gap-5">
      <ProcessingOptions value={opts} onChange={setOpts} idPrefix={`analyze-${source.id}`} />
      <div className="space-y-1.5">
        <Label htmlFor="language">Spoken language</Label>
        <Input id="language" placeholder="auto-detect (or e.g. en, es, de)" value={language} onChange={(e) => setLanguage(e.target.value)} maxLength={8} className="h-9 rounded-xl" />
        {caps.data && !caps.data.whisper.available && <p className="text-xs text-warning">Speech-to-text is not available on this server — processing will fail until it is installed.</p>}
      </div>
      {llmReady && (
        <div className={cn("flex items-start gap-3 rounded-xl border border-white/10 p-3")}>
          <Bot className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div className="flex-1">
            <Label htmlFor="use-llm">Optional: rank with {caps.data?.llm.model ?? "the configured language model"}</Label>
            <p className="mt-0.5 text-xs text-muted-foreground">Off = free local AI only (transcript, audio and visual signals). On = an external API key configured on the server is used.</p>
          </div>
          <Switch id="use-llm" checked={useLlm} onCheckedChange={setUseLlm} />
        </div>
      )}
      <p className="flex gap-2 text-xs text-muted-foreground">
        <Info className="mt-px size-3.5 shrink-0" /> Highlight scores are AI estimates, not predictions of views.
      </p>
      <RightsCheckbox id={`rights-analyze-${source.id}`} checked={rights} onChange={setRights} />
      <DialogFooter>
        <Button variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button onClick={submit} disabled={!rights || errors.length > 0 || analyze.isPending} className="glow-red">
          {analyze.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />} Start processing
        </Button>
      </DialogFooter>
    </div>
  );
}

export function AnalyzeDialog({ source, open, onOpenChange }: { source: SourceOut; open: boolean; onOpenChange: (o: boolean) => void }) {
  const me = useMe();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="font-heading text-lg">Process “{source.title}”</DialogTitle>
          <DialogDescription>Choose how ClipFusion should cut this video.</DialogDescription>
        </DialogHeader>
        {open && !me.isLoading && <AnalyzeForm source={source} settings={me.data?.settings} onDone={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}
