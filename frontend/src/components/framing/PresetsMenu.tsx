"use client";

import { useState } from "react";
import { Bookmark, BookmarkPlus, ChevronDown, Loader2, Star } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ToggleRow } from "@/components/preview/controls";
import { errorMessage, useCreateFramingPreset, useFramingPresets, type FramingPresetOut } from "@/lib/api/editor";
import type { FramingConfig } from "@/lib/framing/types";
import { MODES } from "./ModeSwitcher";

export const modeTitle = (m: string) => MODES.find((x) => x.mode === m)?.title ?? m;

/** Framing presets: save the current config, or apply one (merged into the editor, unsaved). */
export function PresetsMenu({ config, onApply }: { config: FramingConfig; onApply: (preset: FramingPresetOut) => void }) {
  const presets = useFramingPresets();
  const create = useCreateFramingPreset();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [isDefault, setIsDefault] = useState(false);

  const save = async () => {
    try {
      await create.mutateAsync({ name: name.trim(), config, is_default: isDefault });
      toast.success(`Preset “${name.trim()}” saved`);
      setOpen(false);
      setName("");
    } catch (e) {
      toast.error("Could not save the preset", { description: errorMessage(e) });
    }
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger render={<Button variant="outline" size="sm" />}>
          <Bookmark /> Presets <ChevronDown className="opacity-60" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-64">
          <DropdownMenuItem onClick={() => setOpen(true)}>
            <BookmarkPlus /> Save current as preset…
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuLabel>Apply a preset</DropdownMenuLabel>
            {presets.isLoading && <DropdownMenuItem disabled>Loading…</DropdownMenuItem>}
            {presets.data?.length === 0 && <DropdownMenuItem disabled>No presets yet</DropdownMenuItem>}
            {presets.data?.map((p) => (
              <DropdownMenuItem key={p.id} onClick={() => onApply(p)}>
                {p.is_default ? <Star className="text-ig-orange" /> : <Bookmark />}
                <span className="min-w-0 flex-1 truncate">{p.name}</span>
                <span className="text-[10px] text-muted-foreground">{modeTitle(p.mode)}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>Save framing preset</DialogTitle>
            <DialogDescription>
              Saves the {modeTitle(config.mode)} settings. Regions are re-fitted to each clip&apos;s aspect when applied; the AI camera path is recomputed per clip.
            </DialogDescription>
          </DialogHeader>
          <Input autoFocus placeholder="e.g. My gaming layout" value={name} onChange={(e) => setName(e.target.value)} aria-label="Preset name" maxLength={80} />
          <ToggleRow label="Use as default for new clips" checked={isDefault} onChange={setIsDefault} />
          <DialogFooter>
            <Button onClick={save} disabled={!name.trim() || create.isPending}>
              {create.isPending && <Loader2 className="animate-spin" />} Save preset
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
