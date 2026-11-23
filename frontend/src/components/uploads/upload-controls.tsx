"use client";

import { useState } from "react";
import Link from "next/link";
import { Info, Link2, Loader2, UploadCloud } from "lucide-react";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { RightsCheckbox } from "@/components/common/fields";
import { ApiError } from "@/lib/api/client";
import { useMe } from "@/lib/api/hooks/auth";
import { toastError } from "@/lib/api/hooks/common";
import { useAccounts } from "@/lib/api/hooks/publishing";
import { useCreateUploadRun } from "@/lib/api/hooks/uploads";
import type { AccountOut, ClipOut, Privacy, UploadRunCreate } from "@/lib/api/types";
import { clipNumber } from "@/lib/format";
import { cn } from "@/lib/utils";

export const PRIVACY_ITEMS: { value: Privacy; label: string }[] = [
  { value: "private", label: "Private" },
  { value: "unlisted", label: "Unlisted" },
  { value: "public", label: "Public" },
];

export const UPLOAD_IN_PROGRESS_TEXT =
  "An upload run is already running for this YouTube account (uploads go one at a time). Wait for it to finish or cancel it, then start again.";

/** Start a run; explains 409 upload_in_progress instead of a generic error. Returns true on success. */
export async function startRun(create: ReturnType<typeof useCreateUploadRun>, body: UploadRunCreate): Promise<boolean> {
  try {
    await create.mutateAsync(body);
    return true;
  } catch (e) {
    if (e instanceof ApiError && e.code === "upload_in_progress") toast.warning("Upload already in progress", { description: UPLOAD_IN_PROGRESS_TEXT });
    else toastError(e, "Could not start the upload");
    return false;
  }
}

export const canUploadWith = (a: AccountOut | null | undefined) => !!a && a.status === "active" && a.can_publish;

/** Connected YouTube accounts + the selected one (defaults to the first account that can publish). */
export function useUploadTarget() {
  const accounts = useAccounts();
  const me = useMe();
  const youtube = (accounts.data ?? []).filter((a) => a.platform === "youtube");
  const [picked, setPicked] = useState<string | null>(null);
  const [privacyPick, setPrivacy] = useState<Privacy | null>(null);
  const accountId = picked ?? (youtube.find(canUploadWith) ?? youtube[0])?.id ?? null;
  const privacy = privacyPick ?? me.data?.settings.default_privacy ?? "private";
  return {
    accounts: youtube,
    loading: accounts.isLoading,
    accountId,
    setAccountId: setPicked,
    privacy,
    setPrivacy,
    account: youtube.find((a) => a.id === accountId) ?? null,
  };
}

export type UploadTarget = ReturnType<typeof useUploadTarget>;

export function UploadTargetFields({ target, idPrefix }: { target: UploadTarget; idPrefix: string }) {
  if (target.loading) return <Skeleton className="h-20 rounded-xl" />;
  if (!target.accounts.length)
    return (
      <div className="rounded-xl border border-info/25 bg-info/5 p-3 text-sm">
        <p className="font-medium">No YouTube account connected</p>
        <p className="mt-0.5 text-xs text-muted-foreground">Connect the YouTube channel the clips should be uploaded to.</p>
        <Link href="/app/accounts" className={cn(buttonVariants({ variant: "outline", size: "sm" }), "mt-2 rounded-lg")}>
          <Link2 /> Connect an account
        </Link>
      </div>
    );
  const accountItems = target.accounts.map((a) => ({
    value: a.id,
    label: `${a.display_name}${a.username ? ` (@${a.username.replace(/^@/, "")})` : ""}${canUploadWith(a) ? "" : ` · ${a.status}`}`,
  }));
  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-[1.4fr_1fr]">
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-account`}>YouTube account</Label>
          <Select items={accountItems} value={target.accountId ?? ""} onValueChange={(v) => target.setAccountId(v ? String(v) : null)}>
            <SelectTrigger id={`${idPrefix}-account`} className="h-9 w-full rounded-xl">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {accountItems.map((a) => (
                <SelectItem key={a.value} value={a.value}>
                  {a.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`${idPrefix}-privacy`}>Privacy</Label>
          <Select items={PRIVACY_ITEMS} value={target.privacy} onValueChange={(v) => target.setPrivacy((v ?? "private") as Privacy)}>
            <SelectTrigger id={`${idPrefix}-privacy`} className="h-9 w-full rounded-xl">
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
        </div>
      </div>
      {target.account && !canUploadWith(target.account) && (
        <p className="text-xs text-warning">
          This account can&apos;t upload right now ({target.account.last_error || target.account.status}). Reconnect it on the Connected Accounts page.
        </p>
      )}
      <p className="flex gap-1.5 text-xs text-muted-foreground">
        <Info className="mt-px size-3.5 shrink-0" /> Google API projects that haven&apos;t passed Google&apos;s audit can only upload private videos — YouTube keeps
        those uploads private whatever you choose here.
      </p>
    </div>
  );
}

/** Upload one clip now (an upload run in "single" mode). */
export function SingleUploadDialog({ clip, open, onOpenChange }: { clip: ClipOut; open: boolean; onOpenChange: (o: boolean) => void }) {
  const target = useUploadTarget();
  const create = useCreateUploadRun();
  const [rights, setRights] = useState(false);
  const can = canUploadWith(target.account) && rights;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-heading">Upload {clipNumber(clip.sequence)} to YouTube</DialogTitle>
          <DialogDescription>
            Uses the clip&apos;s title, description and hashtags. Once YouTube confirms the video, the clip&apos;s local files are deleted to free space.
          </DialogDescription>
        </DialogHeader>
        <UploadTargetFields target={target} idPrefix={`single-${clip.id}`} />
        <RightsCheckbox id={`rights-single-${clip.id}`} checked={rights} onChange={setRights} />
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!can || create.isPending}
            onClick={async () => {
              if (!target.accountId) return;
              const ok = await startRun(create, {
                video_source_id: clip.video_source_id,
                connected_account_id: target.accountId,
                mode: "single",
                clip_id: clip.id,
                privacy: target.privacy,
                rights_confirmed: true,
              });
              if (ok) {
                toast.success("Upload started");
                onOpenChange(false);
              }
            }}
          >
            {create.isPending ? <Loader2 className="animate-spin" /> : <UploadCloud />} Upload
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
