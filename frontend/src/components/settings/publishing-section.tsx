"use client";

import { Send } from "lucide-react";
import { toast } from "sonner";
import { Switch } from "@/components/ui/switch";
import { SectionCard } from "@/components/common/page";
import { usePublishingPause } from "@/lib/api/hooks/publishing";
import type { UserOut } from "@/lib/api/types";

export function PublishingSection({ user }: { user: UserOut }) {
  const pause = usePublishingPause();
  return (
    <SectionCard title="Publishing" description="Applies to every connected account and schedule." icon={Send}>
      <label className="flex items-center justify-between gap-4 text-sm">
        <span>
          <span className="block font-medium">Pause all publishing</span>
          <span className="block text-xs text-muted-foreground">Nothing is uploaded while paused. Queued posts stay in the calendar.</span>
        </span>
        <Switch
          checked={user.settings.publishing_paused}
          disabled={pause.isPending}
          onCheckedChange={(v) => pause.mutate(v, { onSuccess: () => toast.success(v ? "All publishing paused" : "Publishing resumed") })}
        />
      </label>
    </SectionCard>
  );
}
