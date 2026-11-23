"use client";

import Link from "next/link";
import { ArrowLeft, CheckCircle2, CircleDot, Copy, Loader2, Save } from "lucide-react";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { TipButton } from "@/components/preview/controls";
import { RenderButton } from "@/components/preview/RenderButton";
import type { FramingPresetOut } from "@/lib/api/editor";
import type { FramingConfig } from "@/lib/framing/types";
import { PresetsMenu } from "./PresetsMenu";
import { readApplyToVideo, writeApplyToVideo } from "./useFramingEditor";

export function SaveState({ dirty, saving }: { dirty: boolean; saving: boolean }) {
  if (saving)
    return (
      <span className="flex items-center gap-1 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" /> Saving…
      </span>
    );
  return dirty ? (
    <span className="flex items-center gap-1 text-xs text-warning">
      <CircleDot className="size-3.5" /> Unsaved changes
    </span>
  ) : (
    <span className="flex items-center gap-1 text-xs text-muted-foreground">
      <CheckCircle2 className="size-3.5 text-success" /> All changes saved
    </span>
  );
}

/** Guarded in-app back navigation (confirms when there are unsaved edits). */
export function BackLink({ href, dirty, label }: { href: string; dirty: boolean; label: string }) {
  return (
    <TipButton
      label={label}
      variant="ghost"
      size="icon-sm"
      nativeButton={false}
      render={
        <Link
          href={href}
          onClick={(e) => {
            if (dirty && !window.confirm("You have unsaved changes. Leave without saving?")) e.preventDefault();
          }}
        />
      }
    >
      <ArrowLeft />
    </TipButton>
  );
}

export function FramingTopBar({
  clipId,
  title,
  subtitle,
  dirty,
  saving,
  config,
  onSave,
  onApplyPreset,
  onApplyToOthers,
  onFixFraming,
}: {
  clipId: string;
  title: string;
  subtitle: string;
  dirty: boolean;
  saving: boolean;
  config: FramingConfig;
  onSave: () => Promise<unknown>;
  onApplyPreset: (p: FramingPresetOut) => void;
  onApplyToOthers: () => void;
  onFixFraming: () => void;
}) {
  return (
    <header className="flex flex-wrap items-center gap-3">
      <BackLink href="/app/framing" dirty={dirty} label="Back to framing" />
      <div className="min-w-0 flex-1">
        <h1 className="truncate font-heading text-lg font-semibold tracking-tight">{title}</h1>
        <div className="flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
          <span className="truncate">{subtitle}</span>
          <SaveState dirty={dirty} saving={saving} />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <PresetsMenu config={config} onApply={onApplyPreset} />
        <Button variant="outline" size="sm" onClick={onApplyToOthers}>
          <Copy /> Apply to other clips
        </Button>
        <ApplyToVideoToggle />
        <TipButton label="Save framing" shortcut="Ctrl+S" variant="secondary" onClick={() => void onSave()} disabled={!dirty || saving}>
          {saving ? <Loader2 className="animate-spin" /> : <Save />} Save
        </TipButton>
        <RenderButton clipId={clipId} dirty={dirty} onSave={onSave} onFixFraming={onFixFraming} />
      </div>
    </header>
  );
}

/** Save also applies the framing to every clip of this video (on by default; remembered in this browser). */
function ApplyToVideoToggle() {
  // the editor renders only in the browser (after its data loaded), so localStorage can be read up front
  const [on, setOn] = useState(readApplyToVideo);
  return (
    <label
      className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground"
      title="When you save, the same framing is applied to the other clips of this video"
    >
      <Checkbox
        checked={on}
        onCheckedChange={(v) => {
          setOn(v === true);
          writeApplyToVideo(v === true);
        }}
      />
      All clips of this video
    </label>
  );
}
