"use client";

import { cn } from "@/lib/utils";

import { ColorField, LabeledSlider, Panel, Segmented } from "@/components/preview/controls";
import { useAssets } from "@/lib/api/editor";
import type { Background } from "@/lib/framing/types";

/** Background behind the layers: blurred video, solid colour or an uploaded image asset. */
export function BackgroundControls({ value, onChange }: { value: Background; onChange: (patch: Partial<Background>) => void }) {
  const assets = useAssets("background");
  return (
    <Panel title="Background">
      <div className="space-y-4">
        <Segmented
          ariaLabel="Background type"
          value={value.type}
          className="w-full"
          options={[
            { value: "blur", label: "Blurred video" },
            { value: "color", label: "Colour" },
            { value: "image", label: "Image" },
          ]}
          onChange={(type) => onChange({ type })}
        />
        {value.type === "blur" && (
          <>
            <LabeledSlider label="Blur strength" value={value.blur} min={0} max={200} step={1} format={(v) => `${Math.round(v)}`} onChange={(blur) => onChange({ blur })} />
            <LabeledSlider label="Dim" value={value.dim} min={0} max={1} step={0.01} format={(v) => `${Math.round(v * 100)}%`} onChange={(dim) => onChange({ dim })} />
          </>
        )}
        {value.type === "color" && <ColorField label="Background colour" value={value.color} onChange={(color) => onChange({ color })} />}
        {value.type === "image" && (
          <div className="space-y-2">
            {assets.isLoading && <p className="text-xs text-muted-foreground">Loading background images…</p>}
            {assets.data && assets.data.length === 0 && (
              <p className="text-xs text-muted-foreground">No background images yet — upload one in Assets (kind “background”).</p>
            )}
            <div role="radiogroup" aria-label="Background image" className="grid grid-cols-3 gap-2">
              {(assets.data ?? []).map((a) => (
                <button
                  key={a.id}
                  type="button"
                  role="radio"
                  aria-checked={value.image_asset_id === a.id}
                  title={a.name}
                  onClick={() => onChange({ image_asset_id: a.id })}
                  className={cn(
                    "aspect-[9/16] overflow-hidden rounded-lg bg-muted bg-cover bg-center ring-1 ring-white/10 outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    value.image_asset_id === a.id && "ring-2 ring-ig-pink",
                  )}
                  style={a.url ? { backgroundImage: `url(${JSON.stringify(a.url)})` } : undefined}
                >
                  <span className="sr-only">{a.name}</span>
                </button>
              ))}
            </div>
            {!value.image_asset_id && <p className="text-[11px] text-warning">Choose an image — rendering needs one.</p>}
          </div>
        )}
      </div>
    </Panel>
  );
}

export function useBackgroundImageUrl(bg: Background): string | null {
  const assets = useAssets("background");
  if (bg.type !== "image" || !bg.image_asset_id) return null;
  return assets.data?.find((a) => a.id === bg.image_asset_id)?.url ?? null;
}
