"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { HardDrive, Loader2, Plus, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton } from "@/components/ui/skeleton";
import { PLATFORM_META, PlatformIcon } from "@/components/common/brand";
import { ErrorState, PageHeader, Surface } from "@/components/common/page";
import { qk, toastError } from "@/lib/api/hooks/common";
import { getConnectUrl, useAccounts, useRequirements } from "@/lib/api/hooks/publishing";
import type { AccountOut, Platform, PlatformRequirements } from "@/lib/api/types";
import { titleCase } from "@/lib/format";
import { AccountRow } from "./account-row";
import { RequirementsPanel } from "./requirements-panel";

const INTRO: Record<Platform, string> = {
  youtube: "Publish Shorts with the YouTube Data API and read real retention for your own videos.",
  instagram: "Publish Reels to a professional account with the Instagram Graph API.",
};

/** Shows the OAuth callback outcome (?connected= / ?error=) once, then cleans the URL. */
function useOAuthResult() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const qc = useQueryClient();
  const connected = params.get("connected");
  const error = params.get("error");
  useEffect(() => {
    if (!connected && !error) return;
    if (connected) {
      toast.success(`${PLATFORM_META[connected as Platform]?.label ?? titleCase(connected)} connected`, { id: "oauth-result" });
      qc.invalidateQueries({ queryKey: qk.accounts });
    } else if (error) {
      toast.error("Couldn't connect the account", { id: "oauth-result", description: error });
    }
    router.replace(pathname, { scroll: false });
  }, [connected, error, pathname, qc, router]);
}

function PlatformCard({ platform, accounts, req }: { platform: Platform; accounts: AccountOut[]; req: PlatformRequirements | undefined }) {
  const meta = PLATFORM_META[platform];
  const [drive, setDrive] = useState(false);
  const [pending, setPending] = useState(false);

  const connect = async () => {
    setPending(true);
    try {
      const { authorization_url } = await getConnectUrl(platform, platform === "youtube" && drive);
      window.location.assign(authorization_url);
    } catch (e) {
      toastError(e);
      setPending(false);
    }
  };

  return (
    <Surface className="flex flex-col gap-5 p-5 sm:p-6">
      <div className="flex items-start gap-3">
        <span className="gradient-border grid size-12 shrink-0 place-items-center rounded-2xl bg-black/30">
          <PlatformIcon platform={platform} className="size-6" />
        </span>
        <div>
          <h2 className="font-heading text-lg font-semibold">{meta.product}</h2>
          <p className="text-sm text-muted-foreground">{INTRO[platform]}</p>
        </div>
      </div>

      {accounts.length > 0 ? (
        <ul className="space-y-3">
          {accounts.map((a) => (
            <AccountRow key={a.id} account={a} />
          ))}
        </ul>
      ) : (
        <p className="rounded-2xl border border-dashed border-white/12 px-4 py-6 text-center text-sm text-muted-foreground">No {meta.label} account connected yet.</p>
      )}

      <div className="space-y-3">
        {platform === "youtube" && (
          <label className="flex items-start gap-2.5 text-sm">
            <Checkbox checked={drive} onCheckedChange={(v) => setDrive(v === true)} className="mt-0.5" />
            <span>
              <span className="flex items-center gap-1.5">
                <HardDrive className="size-3.5" /> Also allow Google Drive imports
              </span>
              <span className="block text-xs text-muted-foreground">Read-only access, used to import original videos you choose.</span>
            </span>
          </label>
        )}
        <Button size="lg" className="w-full rounded-xl sm:w-auto" disabled={!req?.configured || pending} onClick={connect}>
          {pending ? <Loader2 className="animate-spin" /> : <Plus />} {accounts.length ? `Connect another ${meta.label} account` : `Connect ${meta.label}`}
        </Button>
        {req && !req.configured && <p className="text-xs text-muted-foreground">Connecting becomes available once the credentials below are added on the server.</p>}
      </div>

      {req && <RequirementsPanel req={req} />}
    </Surface>
  );
}

export function AccountsView() {
  useOAuthResult();
  const accounts = useAccounts();
  const reqs = useRequirements();
  const platforms: Platform[] = ["youtube", "instagram"];

  return (
    <>
      <PageHeader title="Connected Accounts" description="Connect the channels ClipFusion publishes to." />
      <div className="mb-6 flex items-start gap-3 rounded-2xl border border-success/20 bg-success/5 p-4 text-sm">
        <ShieldCheck className="mt-0.5 size-5 shrink-0 text-success" />
        <p>
          <span className="font-medium">ClipFusion never asks for your passwords.</span>{" "}
          <span className="text-muted-foreground">
            You sign in on Google&apos;s or Meta&apos;s own page, and all publishing goes through the official YouTube and Instagram APIs. Access tokens are stored encrypted and you can disconnect at any time.
          </span>
        </p>
      </div>
      {accounts.error ? (
        <ErrorState error={accounts.error} onRetry={() => accounts.refetch()} />
      ) : accounts.isLoading || reqs.isLoading ? (
        <div className="grid gap-6 xl:grid-cols-2">
          <Skeleton className="h-96 rounded-2xl" />
          <Skeleton className="h-96 rounded-2xl" />
        </div>
      ) : (
        <>
          {reqs.error && <ErrorState error={reqs.error} onRetry={() => reqs.refetch()} className="mb-6" />}
          <div className="grid items-start gap-6 xl:grid-cols-2">
            {platforms.map((p) => (
              <PlatformCard key={p} platform={p} accounts={(accounts.data ?? []).filter((a) => a.platform === p)} req={reqs.data?.[p]} />
            ))}
          </div>
        </>
      )}
    </>
  );
}
