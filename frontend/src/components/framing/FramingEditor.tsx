"use client";

/**
 * Smart Framing Editor: ORIGINAL video with editable overlays (left), exact-geometry 9:16
 * output preview (right), mode switcher, per-mode settings, background, timeline and the live
 * crop-protection panel. Data: GET /clips/{id}/framing (+ GET /clips/{id} for the title).
 */
import { use, useRef, useState } from "react";
import { toast } from "sonner";

import { Skeleton } from "@/components/ui/skeleton";
import { ApplyToClipsDialog } from "@/components/preview/ApplyToClipsDialog";
import { ClipPreviewCanvas } from "@/components/preview/ClipPreviewCanvas";
import { Panel, Segmented, TipButton } from "@/components/preview/controls";
import { ExactFrameButton } from "@/components/preview/ExactFrameButton";
import { usePlayerShortcuts, usePlayerState, useClipPlayer } from "@/components/preview/player";
import { useEditorGuards } from "@/components/preview/useEditorGuards";
import { errorMessage, useAcknowledgeFraming, useApplyFraming, useClip, useFraming, type FramingOut } from "@/lib/api/editor";
import { applyConfigPatch, FRAMING_MODES } from "@/lib/framing/layout";
import type { FramingMode } from "@/lib/framing/types";
import { AiAssistPanel } from "./AiAssistPanel";
import { BackgroundControls, useBackgroundImageUrl } from "./BackgroundControls";
import { FramingTimeline } from "./FramingTimeline";
import { FramingTopBar } from "./FramingTopBar";
import { ModeControls } from "./ModeControls";
import { ModeSwitcher } from "./ModeSwitcher";
import { PreviewOverlay } from "./PreviewOverlay";
import { ProtectionPanel } from "./ProtectionPanel";
import { SmartControls } from "./SmartControls";
import { SourcePanel } from "./SourcePanel";
import { trackLabel } from "./SourceOverlays";
import { useFramingEditor } from "./useFramingEditor";

export function FramingEditor({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const framing = useFraming(id);
  if (framing.isError) return <p className="p-8 text-sm text-destructive">Could not load the framing: {errorMessage(framing.error)}</p>;
  if (!framing.data) return <EditorSkeleton />;
  return <Workspace key={id} clipId={id} data={framing.data} />;
}

export function EditorSkeleton() {
  return (
    <div className="space-y-4 p-4" aria-busy>
      <Skeleton className="h-10 w-1/3 rounded-xl" />
      <Skeleton className="h-20 rounded-2xl" />
      <div className="grid gap-4 lg:grid-cols-[1fr_320px_360px]">
        <Skeleton className="h-[420px] rounded-2xl" />
        <Skeleton className="h-[420px] rounded-2xl" />
        <Skeleton className="h-[420px] rounded-2xl" />
      </div>
    </div>
  );
}

function Workspace({ clipId, data }: { clipId: string; data: FramingOut }) {
  const clip = useClip(clipId);
  const editor = useFramingEditor(clipId, data);
  const { config, update } = editor;
  const player = useClipPlayer({ url: data.proxy_url, clipStart: data.clip_start, clipEnd: data.clip_end, proxyStart: data.proxy_start });
  const { time } = usePlayerState(player);
  usePlayerShortcuts(player);
  const acknowledge = useAcknowledgeFraming(clipId);
  const applyMany = useApplyFraming();
  const [applyOpen, setApplyOpen] = useState(false);
  const [showFaces, setShowFaces] = useState(true);
  const [showHeatmap, setShowHeatmap] = useState(false);
  const [guides, setGuides] = useState(false);
  const protectionRef = useRef<HTMLDivElement>(null);
  const bgUrl = useBackgroundImageUrl(config.background);
  const vision = data.vision;
  const sw = data.source.width;
  const sh = data.source.height;
  const { dirty, save } = editor;

  const setMode = (mode: FramingMode) => update((c) => ({ ...c, mode }));
  const doSave = async () => {
    try {
      const out = await save();
      const n = out?.applied_to_clips ?? 0;
      toast.success("Framing saved", n ? { description: `Also applied to the other ${n} clip${n === 1 ? "" : "s"} of this video (re-rendering).` } : undefined);
    } catch (e) {
      toast.error("Save failed", { description: errorMessage(e) });
      throw e;
    }
  };
  useEditorGuards({
    dirty,
    onSave: () => void doSave().catch(() => undefined),
    onKey: (e) => {
      if (!e.ctrlKey && !e.metaKey && !e.altKey && /^[1-5]$/.test(e.key)) setMode(FRAMING_MODES[Number(e.key) - 1]);
    },
  });
  const fixFraming = () => {
    protectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    protectionRef.current?.animate([{ boxShadow: "0 0 0 3px #DD2A7B" }, { boxShadow: "0 0 0 0 transparent" }], { duration: 1200 });
  };
  const trackName = (tid: number) => {
    const i = vision?.tracks.findIndex((t) => t.id === tid) ?? -1;
    return i >= 0 && vision ? trackLabel(vision.tracks[i], i) : `Person #${tid}`;
  };
  return (
    <div className="flex flex-col gap-4 p-4">
      <FramingTopBar
        clipId={clipId}
        title={clip.data?.title || "Untitled clip"}
        subtitle={`${clip.data?.source_title ?? ""} · ${data.clip_duration.toFixed(1)} s · source ${sw}×${sh}`}
        dirty={dirty}
        saving={editor.saving}
        config={config}
        onSave={doSave}
        onApplyPreset={(p) => {
          update((c) =>
            applyConfigPatch(c, { ...p.config, output_width: c.output_width, output_height: c.output_height, smart: { ...p.config.smart, keyframes: [] } }),
          );
          toast.success(`Preset “${p.name}” applied`, { description: "Review the preview and the warnings, then save." });
        }}
        onApplyToOthers={() => setApplyOpen(true)}
        onFixFraming={fixFraming}
      />
      <ModeSwitcher value={config.mode} suggested={vision?.suggested_mode} onChange={setMode} />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="flex min-w-0 flex-col gap-4">
          <div className="grid gap-4 lg:h-[clamp(480px,62vh,760px)] lg:grid-cols-[minmax(0,1fr)_minmax(280px,34%)]">
            <Panel
              className="flex min-h-[360px] flex-col"
              title="Original"
              actions={
                <div className="flex items-center gap-1">
                  <Segmented
                    ariaLabel="Original overlays"
                    value={showHeatmap ? "heat" : showFaces ? "faces" : "none"}
                    options={[
                      { value: "faces", label: "Faces" },
                      { value: "heat", label: "Activity" },
                      { value: "none", label: "Clean" },
                    ]}
                    onChange={(v) => {
                      setShowFaces(v !== "none");
                      setShowHeatmap(v === "heat");
                    }}
                  />
                </div>
              }
            >
              {data.proxy_url ? (
                <SourcePanel player={player} editor={editor} vision={vision} sourceW={sw} sourceH={sh} showFaces={showFaces} showHeatmap={showHeatmap} />
              ) : (
                <p className="m-auto max-w-xs text-center text-xs text-muted-foreground">
                  The preview proxy for this clip is not ready yet. It is created when the source finishes processing.
                </p>
              )}
            </Panel>
            <Panel
              className="flex min-h-[360px] flex-col"
              title="Output 9:16"
              actions={
                <TipButton
                  label="Safe-area & face guides"
                  size="xs"
                  variant={guides ? "secondary" : "ghost"}
                  aria-pressed={guides}
                  onClick={() => setGuides(!guides)}
                >
                  Guides
                </TipButton>
              }
            >
              <ClipPreviewCanvas
                player={player}
                layout={editor.compiled}
                sourceWidth={sw}
                sourceHeight={sh}
                vision={vision}
                showSafeArea={guides}
                showFaces={guides}
                backgroundImageUrl={bgUrl}
                className="flex-1"
              >
                <PreviewOverlay config={config} update={update} sourceW={sw} sourceH={sh} />
              </ClipPreviewCanvas>
              <div className="mt-3 flex justify-center">
                <ExactFrameButton clipId={clipId} player={player} getPayload={() => ({ framing: config })} />
              </div>
            </Panel>
          </div>
          <FramingTimeline player={player} duration={data.clip_duration} vision={vision} warnings={editor.warnings} />
        </div>
        <aside className="flex flex-col gap-4" aria-label="Framing settings">
          <div ref={protectionRef} className="rounded-2xl">
            <ProtectionPanel
              warnings={editor.warnings}
              checking={editor.checking}
              checkError={editor.checkError}
              acknowledged={data.warnings_acknowledged && !dirty}
              acknowledging={acknowledge.isPending || editor.saving}
              onSeek={(t) => player.seek(t)}
              trackName={trackName}
              onApplyFix={(patch, label) => {
                editor.applyPatch(patch);
                toast.success(label, { description: "Fix applied — review the preview and save." });
              }}
              onAcknowledge={async () => {
                try {
                  if (dirty) await save();
                  await acknowledge.mutateAsync();
                  toast.success("Framing marked as reviewed");
                } catch (e) {
                  toast.error("Could not save the review", { description: errorMessage(e) });
                }
              }}
            />
          </div>
          <AiAssistPanel clipId={clipId} vision={vision} onSuggestion={editor.replace} />
          {config.mode === "smart" && <SmartControls player={player} smart={config.smart} vision={vision} onChange={editor.updateSmart} />}
          <ModeControls config={config} update={update} sourceW={sw} sourceH={sh} vision={vision} time={time} />
          <BackgroundControls value={config.background} onChange={(patch) => update((c) => ({ ...c, background: { ...c.background, ...patch } }))} />
        </aside>
      </div>
      <ApplyToClipsDialog
        open={applyOpen}
        onOpenChange={setApplyOpen}
        excludeId={clipId}
        title="Apply this framing to other clips"
        description="Regions are re-fitted to each clip's aspect; smart camera paths and crop warnings are recomputed per clip. Clips with new warnings must be reviewed before rendering."
        confirmLabel="Apply framing"
        onApply={async (ids) => (await applyMany.mutateAsync({ clip_ids: ids, config })).results}
      />
    </div>
  );
}
