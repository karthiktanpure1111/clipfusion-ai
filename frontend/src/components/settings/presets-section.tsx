"use client";

import { useState } from "react";
import Link from "next/link";
import { Check, Crop, Pencil, Star, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { ErrorState, SectionCard } from "@/components/common/page";
import { useFramingPresets, usePresetMutation } from "@/lib/api/hooks/settings";
import { titleCase } from "@/lib/format";
import { cn } from "@/lib/utils";

interface PresetItem {
  id: string;
  name: string;
  hint: string;
  isDefault: boolean;
  readOnly: boolean;
}

function PresetRow({ item }: { item: PresetItem }) {
  const mutate = usePresetMutation();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(item.name);
  const [confirm, setConfirm] = useState(false);

  const rename = () => {
    const next = name.trim();
    if (!next || next === item.name) return setEditing(false);
    mutate.mutate({ id: item.id, patch: { name: next } }, { onSuccess: () => (toast.success("Preset renamed"), setEditing(false)) });
  };

  return (
    <li className="flex items-center gap-2 rounded-xl border border-white/7 bg-black/15 px-3 py-2">
      {editing ? (
        <form
          className="flex flex-1 items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            rename();
          }}
        >
          <Input autoFocus aria-label="Preset name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />
          <Button type="submit" size="icon-sm" aria-label="Save name" disabled={mutate.isPending}>
            <Check />
          </Button>
          <Button
            type="button"
            size="icon-sm"
            variant="ghost"
            aria-label="Cancel rename"
            onClick={() => {
              setName(item.name);
              setEditing(false);
            }}
          >
            <X />
          </Button>
        </form>
      ) : (
        <>
          <div className="min-w-0 flex-1">
            <p className="flex items-center gap-2 truncate text-sm font-medium">
              {item.name}
              {item.isDefault && <span className="rounded-full bg-ig-pink/15 px-1.5 text-[10px] text-[#f07ab4]">Default</span>}
            </p>
            <p className="text-xs text-muted-foreground">{item.hint}</p>
          </div>
          {item.readOnly ? (
            <span className="text-[11px] text-muted-foreground">Built-in</span>
          ) : (
            <>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={item.isDefault ? `${item.name} is the default` : `Make ${item.name} the default`}
                disabled={item.isDefault || mutate.isPending}
                onClick={() => mutate.mutate({ id: item.id, patch: { is_default: true } }, { onSuccess: () => toast.success("Default preset updated") })}
              >
                <Star className={cn(item.isDefault && "fill-current text-[#FCAF45]")} />
              </Button>
              <Button variant="ghost" size="icon-sm" aria-label={`Rename ${item.name}`} onClick={() => setEditing(true)}>
                <Pencil />
              </Button>
              <Button variant="ghost" size="icon-sm" aria-label={`Delete ${item.name}`} onClick={() => setConfirm(true)}>
                <Trash2 />
              </Button>
            </>
          )}
        </>
      )}
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={`Delete preset “${item.name}”?`}
        description="Clips that already use it keep their current settings."
        confirmLabel="Delete preset"
        destructive
        pending={mutate.isPending}
        onConfirm={() => mutate.mutate({ id: item.id, patch: null }, { onSuccess: () => setConfirm(false) })}
      />
    </li>
  );
}

function PresetList({ items, loading, error, refetch }: { items: PresetItem[]; loading: boolean; error: unknown; refetch: () => void }) {
  if (error) return <ErrorState error={error} onRetry={refetch} />;
  if (loading) return <Skeleton className="h-24 rounded-xl" />;
  if (!items.length) return <p className="text-sm text-muted-foreground">No presets saved yet. Save one from the editor to reuse it on other clips.</p>;
  return (
    <ul className="max-h-80 space-y-1.5 overflow-y-auto pr-1">
      {items.map((i) => (
        <PresetRow key={i.id} item={i} />
      ))}
    </ul>
  );
}

export function PresetsSection() {
  const framing = useFramingPresets();
  const link = (href: string) => (
    <Link href={href} className={cn(buttonVariants({ variant: "ghost", size: "sm" }))}>
      Open editor
    </Link>
  );
  return (
    <div className="grid gap-6">
      <SectionCard title="Framing presets" icon={Crop} actions={link("/app/framing")}>
        <PresetList
          loading={framing.isLoading}
          error={framing.error}
          refetch={() => framing.refetch()}
          items={(framing.data ?? []).map((p) => ({ id: p.id, name: p.name, hint: titleCase(p.mode), isDefault: p.is_default, readOnly: false }))}
        />
      </SectionCard>
    </div>
  );
}
