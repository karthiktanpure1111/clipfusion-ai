"use client";

import { useState } from "react";
import { Loader2, RefreshCcw, Save, Type } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { InstagramIcon, YouTubeIcon } from "@/components/common/brand";
import { ChipsInput } from "@/components/common/fields";
import { SectionCard } from "@/components/common/page";
import { useRegenerateMetadata, useUpdateClip } from "@/lib/api/hooks/clips";
import { useCapabilities } from "@/lib/api/hooks/sources";
import type { ClipDetail, ClipMetadata } from "@/lib/api/types";

const PRIVACY = [
  { value: "public", label: "Public" },
  { value: "unlisted", label: "Unlisted" },
  { value: "private", label: "Private" },
];

function Counter({ value, max }: { value: string; max: number }) {
  return <span className={`text-[11px] tabular-nums ${value.length > max ? "text-[#ff5c7a]" : "text-muted-foreground"}`}>{value.length}/{max}</span>;
}

export function MetadataEditor({ clip }: { clip: ClipDetail }) {
  const update = useUpdateClip(clip.id);
  const regen = useRegenerateMetadata(clip.id);
  const caps = useCapabilities();
  const [m, setM] = useState<ClipMetadata>(clip.social_metadata);
  const yt = m.youtube;
  const ig = m.instagram;
  const setYt = (p: Partial<ClipMetadata["youtube"]>) => setM((c) => ({ ...c, youtube: { ...c.youtube, ...p } }));
  const setIg = (p: Partial<ClipMetadata["instagram"]>) => setM((c) => ({ ...c, instagram: { ...c.instagram, ...p } }));
  const dirty = JSON.stringify([m.youtube, m.instagram]) !== JSON.stringify([clip.social_metadata.youtube, clip.social_metadata.instagram]);
  const regenerating = clip.active_jobs.some((j) => j.job_type === "metadata");
  const tooLong = yt.title.length > 100 || yt.description.length > 5000 || ig.caption.length > 2200;

  return (
    <SectionCard
      title="Title, description & hashtags"
      description={
        m.source === "llm" ? "Written by the AI — edit anything before uploading." : m.source === "user" ? "Edited by you." : "Written by the local AI from the transcript — edit freely."
      }
      icon={Type}
      actions={
        <Button
          variant="outline"
          size="sm"
          disabled={regen.isPending || regenerating}
          onClick={() => regen.mutate(undefined, { onSuccess: () => toast.info("Regenerating metadata… this card updates when it's done.") })}
          title={caps.data && !caps.data.llm.configured ? "Generated locally from the transcript" : undefined}
        >
          {regen.isPending || regenerating ? <Loader2 className="animate-spin" /> : <RefreshCcw />} Regenerate
        </Button>
      }
    >
      <Tabs defaultValue="youtube">
        <TabsList className="mb-4 rounded-xl">
          <TabsTrigger value="youtube"><YouTubeIcon /> YouTube Shorts</TabsTrigger>
          <TabsTrigger value="instagram"><InstagramIcon /> Instagram Reels</TabsTrigger>
        </TabsList>
        <TabsContent value="youtube" className="space-y-4">
          <div className="space-y-1.5">
            <div className="flex justify-between"><Label htmlFor="yt-title">Title</Label><Counter value={yt.title} max={100} /></div>
            <Input id="yt-title" value={yt.title} onChange={(e) => setYt({ title: e.target.value })} className="h-9 rounded-xl" />
          </div>
          <div className="space-y-1.5">
            <div className="flex justify-between"><Label htmlFor="yt-desc">Description</Label><Counter value={yt.description} max={5000} /></div>
            <Textarea id="yt-desc" rows={4} value={yt.description} onChange={(e) => setYt({ description: e.target.value })} className="rounded-xl" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="yt-hook">Opening hook</Label>
            <Input id="yt-hook" value={yt.hook} onChange={(e) => setYt({ hook: e.target.value })} className="h-9 rounded-xl" />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="yt-tags">Tags</Label>
              <ChipsInput id="yt-tags" value={yt.tags} onChange={(tags) => setYt({ tags })} placeholder="Add tag, press Enter" max={100} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="yt-hashtags">Hashtags</Label>
              <ChipsInput id="yt-hashtags" prefix="#" value={yt.hashtags} onChange={(hashtags) => setYt({ hashtags })} placeholder="#shorts" max={60} />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-6">
            <div className="space-y-1.5">
              <Label htmlFor="yt-privacy">Privacy</Label>
              <Select items={PRIVACY} value={yt.privacy} onValueChange={(v) => setYt({ privacy: (v ?? "public") as ClipMetadata["youtube"]["privacy"] })}>
                <SelectTrigger id="yt-privacy" className="h-9 w-40 rounded-xl"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {PRIVACY.map((p) => <SelectItem key={p.value} value={p.value}>{p.label}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <label className="flex items-center gap-2 pt-5 text-sm">
              <Switch checked={yt.made_for_kids} onCheckedChange={(v) => setYt({ made_for_kids: v })} /> Made for kids
            </label>
          </div>
        </TabsContent>
        <TabsContent value="instagram" className="space-y-4">
          <div className="space-y-1.5">
            <div className="flex justify-between"><Label htmlFor="ig-caption">Caption</Label><Counter value={ig.caption} max={2200} /></div>
            <Textarea id="ig-caption" rows={5} value={ig.caption} onChange={(e) => setIg({ caption: e.target.value })} className="rounded-xl" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ig-hook">Opening hook</Label>
            <Input id="ig-hook" value={ig.hook} onChange={(e) => setIg({ hook: e.target.value })} className="h-9 rounded-xl" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ig-hashtags">Hashtags</Label>
            <ChipsInput id="ig-hashtags" prefix="#" value={ig.hashtags} onChange={(hashtags) => setIg({ hashtags })} placeholder="#reels" max={30} />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <Switch checked={ig.share_to_feed} onCheckedChange={(v) => setIg({ share_to_feed: v })} /> Also share to main feed
          </label>
        </TabsContent>
      </Tabs>
      <div className="mt-5 flex gap-2">
        <Button
          disabled={!dirty || tooLong || update.isPending}
          onClick={() =>
            update.mutate({ social_metadata: { youtube: m.youtube, instagram: m.instagram } }, {
              onSuccess: (d) => {
                setM(d.social_metadata); // the server normalises tags and hashtags
                toast.success("Metadata saved");
              },
            })
          }
        >
          {update.isPending ? <Loader2 className="animate-spin" /> : <Save />} Save metadata
        </Button>
        <Button variant="ghost" disabled={!dirty} onClick={() => setM(clip.social_metadata)}>
          Discard changes
        </Button>
      </div>
    </SectionCard>
  );
}
