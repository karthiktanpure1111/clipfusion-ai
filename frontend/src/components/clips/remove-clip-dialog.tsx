"use client";

import { toast } from "sonner";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { useRemoveClip } from "@/lib/api/hooks/clips";
import type { ClipOut } from "@/lib/api/types";
import { clipNumber, formatBytes } from "@/lib/format";

/** Remove Clip: deletes only this clip's files (MP4, proxy, thumbnail, temp); a small record stays. */
export function RemoveClipDialog({
  clip,
  open,
  onOpenChange,
  onRemoved,
}: {
  clip: ClipOut;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onRemoved?: () => void;
}) {
  const remove = useRemoveClip();
  const size = clip.media_bytes ? ` (about ${formatBytes(clip.media_bytes)})` : "";
  const uploading = clip.status === "uploading";
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Remove ${clipNumber(clip.sequence)}?`}
      description={
        uploading
          ? "This clip is uploading to YouTube right now. Wait for the upload to finish (or cancel the run) before removing it."
          : `This permanently deletes this clip's files from this computer: the rendered MP4, its preview proxy, its thumbnail and any temporary files${size}. ` +
            "A small record stays (title, timestamps and upload history) and the clip disappears from the timeline. " +
            "The original video, the other clips and anything already published on YouTube are not touched."
      }
      confirmLabel={uploading ? "Close" : "Remove clip"}
      destructive={!uploading}
      pending={remove.isPending}
      onConfirm={() =>
        uploading ? onOpenChange(false) : remove.mutate(clip.id, {
          onSuccess: (r) => {
            const freed = r && typeof r === "object" ? r.freed_bytes : null;
            toast.success(`${clipNumber(clip.sequence)} removed`, freed ? { description: `Freed ${formatBytes(freed)}` } : undefined);
            onOpenChange(false);
            onRemoved?.();
          },
        })
      }
    />
  );
}
