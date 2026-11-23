"use client";

import { useRef, useState } from "react";
import { ImageIcon, Loader2, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { ErrorState, SectionCard } from "@/components/common/page";
import { Segmented } from "@/components/common/segmented";
import { useAssets, useDeleteAsset, useUploadAsset } from "@/lib/api/hooks/settings";
import type { AssetKind, AssetOut } from "@/lib/api/types";
import { formatBytes, formatDuration } from "@/lib/format";

const KINDS: { value: AssetKind; label: string; accept: string; hint: string }[] = [
  { value: "intro", label: "Intro", accept: "video/*", hint: "A short video added before the clip." },
  { value: "outro", label: "Outro", accept: "video/*", hint: "A short video added after the clip." },
  { value: "watermark", label: "Watermark", accept: "image/png,image/jpeg,image/webp", hint: "PNG with transparency works best." },
  { value: "background", label: "Background", accept: "image/png,image/jpeg,image/webp", hint: "Shown behind full-frame layouts." },
];

function Preview({ asset }: { asset: AssetOut }) {
  if (!asset.url) return <ImageIcon className="size-5 text-white/30" />;
  if (asset.mime_type.startsWith("video/")) return <video src={asset.url} muted playsInline preload="metadata" className="size-full object-contain" aria-label={asset.name} />;
  // eslint-disable-next-line @next/next/no-img-element -- signed media URL from the API
  return <img src={asset.url} alt={asset.name} className="size-full object-contain" />;
}

export function AssetsSection() {
  const assets = useAssets();
  const upload = useUploadAsset();
  const del = useDeleteAsset();
  const ref = useRef<HTMLInputElement>(null);
  const [kind, setKind] = useState<AssetKind>("intro");
  const [toDelete, setToDelete] = useState<AssetOut | null>(null);
  const meta = KINDS.find((k) => k.value === kind)!;
  const items = (assets.data ?? []).filter((a) => a.kind === kind);

  return (
    <SectionCard title="Brand assets" description="Intros, outros, watermarks and backgrounds you can use when rendering." icon={ImageIcon}>
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <Segmented
          ariaLabel="Asset type"
          size="sm"
          className="overflow-x-auto"
          value={kind}
          onChange={setKind}
          options={KINDS.map((k) => ({ value: k.value, label: k.label, count: assets.data?.filter((a) => a.kind === k.value).length }))}
        />
        <input
          ref={ref}
          type="file"
          accept={meta.accept}
          className="hidden"
          aria-label={`${meta.label} file`}
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (file) upload.mutate({ file, kind }, { onSuccess: () => toast.success(`${meta.label} uploaded`) });
          }}
        />
        <Button variant="outline" onClick={() => ref.current?.click()} disabled={upload.isPending}>
          {upload.isPending ? <Loader2 className="animate-spin" /> : <Upload />} Upload {meta.label.toLowerCase()}
        </Button>
      </div>
      <p className="mb-3 text-xs text-muted-foreground">{meta.hint}</p>
      {assets.error ? (
        <ErrorState error={assets.error} onRetry={() => assets.refetch()} />
      ) : assets.isLoading ? (
        <Skeleton className="h-28 rounded-xl" />
      ) : items.length ? (
        <ul className="grid grid-cols-2 gap-3 md:grid-cols-3">
          {items.map((a) => (
            <li key={a.id} className="overflow-hidden rounded-xl border border-white/7 bg-black/15">
              <div className="checkerboard grid aspect-video place-items-center">
                <Preview asset={a} />
              </div>
              <div className="flex items-center gap-2 p-2.5">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{a.name}</p>
                  <p className="truncate text-[11px] text-muted-foreground">
                    {formatBytes(a.file_size)}
                    {a.duration ? ` · ${formatDuration(a.duration)}` : ""}
                    {a.width ? ` · ${a.width}×${a.height}` : ""}
                  </p>
                </div>
                <Button variant="ghost" size="icon-sm" aria-label={`Delete ${a.name}`} onClick={() => setToDelete(a)}>
                  <Trash2 />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="rounded-xl border border-dashed border-white/10 py-8 text-center text-sm text-muted-foreground">No {meta.label.toLowerCase()} uploaded yet.</p>
      )}
      <ConfirmDialog
        open={!!toDelete}
        onOpenChange={(o) => !o && setToDelete(null)}
        title={`Delete “${toDelete?.name ?? ""}”?`}
        description="Clips that use this asset will render without it."
        confirmLabel="Delete asset"
        destructive
        pending={del.isPending}
        onConfirm={() => toDelete && del.mutate(toDelete.id, { onSuccess: () => setToDelete(null) })}
      />
    </SectionCard>
  );
}
