"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState, PageHeader } from "@/components/common/page";
import { useMe } from "@/lib/api/hooks/auth";
import { AssetsSection } from "./assets-section";
import { CapabilitiesPanel } from "./capabilities-panel";
import { PresetsSection } from "./presets-section";
import { DefaultsSection, ProfileSection } from "./profile-defaults";
import { PublishingSection } from "./publishing-section";
import { StoragePanel } from "@/components/storage/storage-panel";

const SECTIONS = [
  ["profile", "Profile"],
  ["defaults", "Processing defaults"],
  ["storage", "Storage"],
  ["publishing", "Publishing"],
  ["presets", "Framing presets"],
  ["assets", "Brand assets"],
  ["system", "System"],
] as const;

function Anchor({ id, children }: { id: string; children: React.ReactNode }) {
  return (
    <div id={id} className="scroll-mt-24">
      {children}
    </div>
  );
}

export function SettingsView() {
  const { data: user, isLoading, error, refetch } = useMe();
  return (
    <>
      <PageHeader title="Settings" description="Your profile, processing and upload defaults, storage, framing presets and brand assets." />
      <div className="grid gap-8 xl:grid-cols-[180px_1fr]">
        <nav aria-label="Settings sections" className="flex gap-1 overflow-x-auto xl:sticky xl:top-24 xl:h-fit xl:flex-col">
          {SECTIONS.map(([id, label]) => (
            <a
              key={id}
              href={`#${id}`}
              className="shrink-0 rounded-lg px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-white/4 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              {label}
            </a>
          ))}
        </nav>
        <div className="min-w-0 space-y-6">
          {error ? (
            <ErrorState error={error} onRetry={() => refetch()} />
          ) : isLoading || !user ? (
            <>
              <Skeleton className="h-48 rounded-2xl" />
              <Skeleton className="h-72 rounded-2xl" />
            </>
          ) : (
            <>
              <Anchor id="profile">
                <ProfileSection key={`${user.full_name}-${user.timezone}`} user={user} />
              </Anchor>
              <Anchor id="defaults">
                <DefaultsSection key={JSON.stringify(user.settings)} user={user} />
              </Anchor>
              <Anchor id="publishing">
                <PublishingSection user={user} />
              </Anchor>
            </>
          )}
          <Anchor id="storage">
            <StoragePanel />
          </Anchor>
          <Anchor id="presets">
            <PresetsSection />
          </Anchor>
          <Anchor id="assets">
            <AssetsSection />
          </Anchor>
          <Anchor id="system">
            <CapabilitiesPanel />
          </Anchor>
        </div>
      </div>
    </>
  );
}
