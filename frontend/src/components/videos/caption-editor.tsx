"use client";

import { useState } from "react";
import { Check, Loader2, Pencil, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useUpdateClip } from "@/lib/api/hooks/clips";
import type { ClipMetadata, ClipOut } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const MAX = 100; // YouTube title limit (the Short's caption)

/**
 * The clip's caption (the title the Short is uploaded with), editable in place.
 * Saving keeps the description and hashtags as they are.
 */
export function CaptionEditor({ clip, caption, meta, locked }: {
  clip: ClipOut;
  caption: string;
  meta: ClipMetadata | undefined;
  /** already on YouTube: editing here would not change the published video */
  locked: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(caption);
  const update = useUpdateClip(clip.id);
  const text = draft.trim();
  const valid = text.length > 0 && text.length <= MAX;

  const start = () => {
    setDraft(caption);
    setEditing(true);
  };
  const save = () => {
    if (!valid || !meta) return;
    if (text === caption) return setEditing(false);
    update.mutate(
      { title: text, social_metadata: { ...meta, youtube: { ...meta.youtube, title: text } } },
      {
        onSuccess: () => {
          setEditing(false);
          toast.success("Caption saved", { description: "It will be used when this clip is uploaded." });
        },
      },
    );
  };

  if (!editing) {
    return (
      <div className="group flex items-start gap-2">
        <p className="font-medium leading-snug">{caption}</p>
        {!locked && (
          <Button
            variant="ghost"
            size="xs"
            onClick={start}
            disabled={!meta}
            aria-label="Edit caption"
            title="Edit caption"
            className="shrink-0 text-muted-foreground hover:text-foreground"
          >
            <Pencil /> Edit
          </Button>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-1.5">
      <label htmlFor={`caption-${clip.id}`} className="text-xs font-medium text-muted-foreground">
        Caption (used as the Short&apos;s title)
      </label>
      <Textarea
        id={`caption-${clip.id}`}
        value={draft}
        autoFocus
        rows={2}
        maxLength={MAX + 20}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            save();
          }
          if (e.key === "Escape") setEditing(false);
        }}
        aria-invalid={!valid || undefined}
        className="min-h-0 rounded-xl text-sm"
      />
      <div className="flex items-center gap-2">
        <span className={cn("mr-auto text-xs tabular-nums", text.length > MAX ? "text-destructive" : "text-muted-foreground")}>
          {text.length}/{MAX}
        </span>
        <Button variant="ghost" size="sm" onClick={() => setEditing(false)} disabled={update.isPending}>
          <X /> Cancel
        </Button>
        <Button size="sm" onClick={save} disabled={!valid || update.isPending}>
          {update.isPending ? <Loader2 className="animate-spin" /> : <Check />} Save caption
        </Button>
      </div>
    </div>
  );
}
