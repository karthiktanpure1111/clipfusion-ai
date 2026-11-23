"use client";

/** /app/framing — recent clips with their framing mode and warnings + saved framing presets. */
import { useState } from "react";
import { AlertTriangle, Check, Crop, Loader2, Star, Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { ClipEntryGrid, MetaChip } from "@/components/preview/ClipEntryGrid";
import { Panel, TipButton } from "@/components/preview/controls";
import { errorMessage, useDeleteFramingPreset, useFramingPresets, useUpdateFramingPreset, type FramingPresetOut } from "@/lib/api/editor";
import { modeTitle } from "./PresetsMenu";

export function FramingEntry() {
  return (
    <div className="space-y-6 p-4 md:p-6">
      <header>
        <h1 className="flex items-center gap-2 font-heading text-2xl font-semibold tracking-tight">
          <span className="bg-ig-gradient grid size-9 place-items-center rounded-xl text-white">
            <Crop className="size-5" />
          </span>
          Smart Framing
        </h1>
        <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
          Choose a clip to frame it for 9:16. Faces and important content are checked before every export — warnings are always shown.
        </p>
      </header>
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
        <ClipEntryGrid
          section="framing"
          renderMeta={(c) => (
            <>
              <MetaChip>{c.framing_mode ? modeTitle(c.framing_mode) : "Not framed yet"}</MetaChip>
              {c.warning_count > 0 ? (
                <MetaChip tone={c.blocking_warning_count && !c.warnings_acknowledged ? "warning" : "muted"}>
                  <AlertTriangle className="size-3" aria-hidden />
                  {c.warning_count} warning{c.warning_count === 1 ? "" : "s"}
                  {c.warnings_acknowledged && " · reviewed"}
                </MetaChip>
              ) : (
                c.framing_mode && (
                  <MetaChip tone="success">
                    <Check className="size-3" aria-hidden /> No warnings
                  </MetaChip>
                )
              )}
            </>
          )}
        />
        <FramingPresets />
      </div>
    </div>
  );
}

function FramingPresets() {
  const presets = useFramingPresets();
  const remove = useDeleteFramingPreset();
  const update = useUpdateFramingPreset();
  const [deleting, setDeleting] = useState<FramingPresetOut | null>(null);
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      toast.success(ok);
    } catch (e) {
      toast.error("Preset update failed", { description: errorMessage(e) });
    }
  };
  return (
    <Panel title="Framing presets" className="h-fit">
      {presets.isLoading && <Loader2 className="size-4 animate-spin text-muted-foreground" aria-label="Loading presets" />}
      {presets.isError && <p className="text-xs text-destructive">{errorMessage(presets.error)}</p>}
      {presets.data?.length === 0 && (
        <p className="text-xs text-muted-foreground">No presets yet. Open a clip, set up its framing, then use Presets → “Save current as preset”.</p>
      )}
      <ul className="space-y-1.5">
        {presets.data?.map((p) => (
          <li key={p.id} className="flex items-center gap-2 rounded-xl bg-muted/50 px-3 py-2">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{p.name}</p>
              <p className="text-[11px] text-muted-foreground">
                {modeTitle(p.mode)}
                {p.is_default && " · default for new clips"}
              </p>
            </div>
            <TipButton
              label={p.is_default ? "Remove as default" : "Use as default for new clips"}
              variant="ghost"
              size="icon-sm"
              aria-pressed={p.is_default}
              disabled={update.isPending}
              onClick={() => run(() => update.mutateAsync({ id: p.id, is_default: !p.is_default }), "Default updated")}
            >
              <Star className={p.is_default ? "fill-ig-orange text-ig-orange" : ""} />
            </TipButton>
            <TipButton label="Delete preset" variant="ghost" size="icon-sm" onClick={() => setDeleting(p)}>
              <Trash2 />
            </TipButton>
          </li>
        ))}
      </ul>
      <AlertDialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{deleting?.name}”?</AlertDialogTitle>
            <AlertDialogDescription>Clips that already use this framing keep their settings.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (!deleting) return;
                void run(() => remove.mutateAsync(deleting.id), "Preset deleted");
                setDeleting(null);
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Panel>
  );
}
