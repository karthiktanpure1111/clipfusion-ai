"use client";

import { useState } from "react";
import { Loader2, Save, SlidersHorizontal, UserRound } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { TimezoneSelect } from "@/components/common/fields";
import { SectionCard } from "@/components/common/page";
import { useUpdateMe } from "@/lib/api/hooks/auth";
import { useCapabilities } from "@/lib/api/hooks/sources";
import { useFramingPresets } from "@/lib/api/hooks/settings";
import type { AnalysisRequest, Privacy, UserOut, UserSettings, YouTubeDownloadQuality } from "@/lib/api/types";
import { PRIVACY_ITEMS } from "@/components/uploads/upload-controls";

import { analysisErrors, defaultAnalysis, ProcessingOptions } from "@/components/videos/processing-options";

const YT_QUALITY = [
  { value: "best", label: "Best available (up to 4K 60fps)" },
  { value: "2160", label: "Up to 2160p (4K)" },
  { value: "1440", label: "Up to 1440p (2K)" },
  { value: "1080", label: "Up to 1080p (Full HD)" },
  { value: "720", label: "Up to 720p (smaller, faster)" },
];

const NONE = "none";

export function ProfileSection({ user }: { user: UserOut }) {
  const update = useUpdateMe();
  const [name, setName] = useState(user.full_name);
  const [tz, setTz] = useState(user.timezone);
  const dirty = name.trim() !== user.full_name || tz !== user.timezone;
  return (
    <SectionCard title="Profile" description="Your name and the time zone used for schedules, charts and dates." icon={UserRound}>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="profile-name">Name</Label>
          <Input id="profile-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} className="h-9 rounded-xl" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="profile-tz">Time zone</Label>
          <TimezoneSelect id="profile-tz" value={tz} onChange={setTz} className="rounded-xl" />
        </div>
      </div>
      <Button
        className="mt-4"
        disabled={!dirty || !name.trim() || update.isPending}
        onClick={() => update.mutateAsync({ full_name: name.trim(), timezone: tz }).then(() => toast.success("Profile saved"), () => undefined)}
      >
        {update.isPending ? <Loader2 className="animate-spin" /> : <Save />} Save profile
      </Button>
    </SectionCard>
  );
}

function PresetSelect({ id, label, value, onChange, presets }: { id: string; label: string; value: string | null; onChange: (v: string | null) => void; presets: { id: string; name: string }[] }) {
  const items = [{ value: NONE, label: "None (automatic)" }, ...presets.map((p) => ({ value: p.id, label: p.name }))];
  const current = value && presets.some((p) => p.id === value) ? value : NONE;
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Select items={items} value={current} onValueChange={(v) => onChange(!v || v === NONE ? null : String(v))}>
        <SelectTrigger id={id} className="h-9 w-full rounded-xl">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {items.map((i) => (
            <SelectItem key={i.value} value={i.value}>
              {i.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

export function DefaultsSection({ user }: { user: UserOut }) {
  const update = useUpdateMe();
  const caps = useCapabilities();
  const framing = useFramingPresets();
  const [s, setS] = useState(user.settings);
  const [opts, setOpts] = useState<AnalysisRequest>(() => defaultAnalysis(user.settings));
  const llm = caps.data?.llm.configured ?? false;
  const privacy = s.default_privacy ?? "private";
  const withDefaults = (base: UserSettings, a: AnalysisRequest, p: Privacy): UserSettings => ({
    ...base,
    default_processing_mode: a.mode,
    default_min_duration: a.min_duration,
    default_max_duration: a.max_duration,
    default_max_clips: a.max_clips,
    default_privacy: p,
    auto_delete_original: !!base.auto_delete_original,
    youtube_download_enabled: !!base.youtube_download_enabled,
    youtube_download_quality: base.youtube_download_quality ?? "best",
  });
  const next = withDefaults(s, opts, privacy);
  const before = withDefaults(user.settings, defaultAnalysis(user.settings), user.settings.default_privacy ?? "private");
  const dirty = JSON.stringify(next) !== JSON.stringify(before);
  const invalid = analysisErrors(opts).length > 0;

  return (
    <SectionCard title="Processing & upload defaults" description="Pre-filled whenever you add or re-process a video and when you upload clips." icon={SlidersHorizontal}>
      <div className="space-y-6">
        <ProcessingOptions value={opts} onChange={setOpts} idPrefix="def" />
        <div className="grid gap-4 sm:grid-cols-2">
          <PresetSelect id="def-framing" label="Framing preset" value={s.default_framing_preset_id} onChange={(v) => setS({ ...s, default_framing_preset_id: v })} presets={framing.data ?? []} />
          <div className="space-y-1.5">
            <Label htmlFor="def-privacy">Default YouTube privacy</Label>
            <Select items={PRIVACY_ITEMS} value={privacy} onValueChange={(v) => setS({ ...s, default_privacy: (v ?? "private") as Privacy })}>
              <SelectTrigger id="def-privacy" className="h-9 w-full rounded-xl">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PRIVACY_ITEMS.map((p) => (
                  <SelectItem key={p.value} value={p.value}>
                    {p.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">Unaudited Google API projects can only upload private videos.</p>
          </div>
        </div>
        <label className="flex items-start gap-3 text-sm">
          <Switch checked={!!s.auto_delete_original} onCheckedChange={(v) => setS({ ...s, auto_delete_original: v })} />
          <span>
            Delete original video automatically after all its clips are uploaded or removed
            <span className="block text-xs text-muted-foreground">Off by default. Only the original long video (and its proxies) is deleted; clip records and YouTube links stay.</span>
          </span>
        </label>
        <div className="space-y-3 rounded-xl border border-warning/25 bg-warning/5 p-3">
          <label className="flex items-start gap-3 text-sm">
            <Switch checked={!!s.youtube_download_enabled} onCheckedChange={(v) => setS({ ...s, youtube_download_enabled: v })} />
            <span>
              Download from YouTube links
              <span className="block text-xs text-muted-foreground">
                Paste a YouTube link and ClipFusion downloads the video (public videos only) and starts processing. YouTube&apos;s Terms of
                Service don&apos;t allow downloading without YouTube&apos;s permission, even when the creator allows clipping, and YouTube may
                block or slow your connection — you take that risk by turning this on.
              </span>
            </span>
          </label>
          {s.youtube_download_enabled && (
            <div className="flex items-center gap-3 pl-12 text-sm">
              <Label htmlFor="yt-quality">Download quality</Label>
              <Select items={YT_QUALITY} value={s.youtube_download_quality ?? "best"} onValueChange={(v) => setS({ ...s, youtube_download_quality: (v ?? "best") as YouTubeDownloadQuality })}>
                <SelectTrigger id="yt-quality" className="h-9 w-64 rounded-xl">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {YT_QUALITY.map((q) => (
                    <SelectItem key={q.value} value={q.value}>
                      {q.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
        </div>
        {llm && (
          <label className="flex items-start gap-3 text-sm">
            <Switch checked={s.default_use_llm} onCheckedChange={(v) => setS({ ...s, default_use_llm: v })} />
            <span>
              Use the configured language model ({caps.data?.llm.model ?? "external API"}) for ranking and titles
              <span className="block text-xs text-muted-foreground">Off = free local AI only.</span>
            </span>
          </label>
        )}
        <Button disabled={!dirty || invalid || update.isPending} onClick={() => update.mutateAsync({ settings: next }).then(() => toast.success("Defaults saved"), () => undefined)}>
          {update.isPending ? <Loader2 className="animate-spin" /> : <Save />} Save defaults
        </Button>
      </div>
    </SectionCard>
  );
}
