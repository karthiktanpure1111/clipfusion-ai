"use client";

import { useState } from "react";
import { ChevronDown, Loader2, RefreshCw, Unplug } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { PlatformIcon } from "@/components/common/brand";
import { ConfirmDialog } from "@/components/common/confirm-dialog";
import { StatusBadge } from "@/components/common/status-badge";
import { useUserTz } from "@/lib/api/hooks/auth";
import { useCheckAccount, useDisconnectAccount } from "@/lib/api/hooks/publishing";
import type { AccountOut } from "@/lib/api/types";
import { formatDateTime, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

/** "https://www.googleapis.com/auth/youtube.upload" -> "youtube.upload" */
const shortScope = (s: string) => s.split("/").filter(Boolean).pop() ?? s;

export function AccountRow({ account }: { account: AccountOut }) {
  const tz = useUserTz();
  const check = useCheckAccount();
  const disconnect = useDisconnectAccount();
  const [confirm, setConfirm] = useState(false);
  const [details, setDetails] = useState(false);

  return (
    <li className="space-y-3 rounded-2xl border border-white/8 bg-black/20 p-4">
      <div className="flex items-center gap-3">
        <span className="relative shrink-0">
          {account.avatar_url ? (
            // eslint-disable-next-line @next/next/no-img-element -- remote platform avatar
            <img src={account.avatar_url} alt="" referrerPolicy="no-referrer" className="size-11 rounded-full object-cover ring-1 ring-white/15" />
          ) : (
            <span className="grid size-11 place-items-center rounded-full bg-white/6 font-heading font-bold">{account.display_name.slice(0, 1).toUpperCase()}</span>
          )}
          <PlatformIcon platform={account.platform} className="absolute -right-1 -bottom-1 size-4.5" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{account.display_name}</p>
          <p className="truncate text-xs text-muted-foreground">{account.username ? `@${account.username.replace(/^@/, "")}` : account.external_account_id}</p>
        </div>
        <StatusBadge status={account.status} />
      </div>

      {!account.can_publish && <p className="rounded-lg bg-warning/8 p-2.5 text-xs text-warning">This account can&apos;t publish right now. Reconnect it and grant the publishing permission.</p>}
      {account.last_error && <p className="rounded-lg bg-destructive/8 p-2.5 text-xs break-words text-[#ff8098]">{account.last_error}</p>}

      <div className="flex flex-wrap items-center gap-1.5">
        <Button
          variant="outline"
          size="sm"
          disabled={check.isPending}
          onClick={() =>
            check.mutate(account.id, {
              onSuccess: (a) => (a.status === "active" ? toast.success(`${a.display_name} is working`) : toast.warning(a.last_error ?? "This account needs attention")),
            })
          }
        >
          {check.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} Check
        </Button>
        <Button variant="ghost" size="sm" onClick={() => setConfirm(true)}>
          <Unplug /> Disconnect
        </Button>
        <button
          type="button"
          aria-expanded={details}
          onClick={() => setDetails((d) => !d)}
          className="ml-auto flex items-center gap-1 rounded text-xs text-muted-foreground hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          Details <ChevronDown className={cn("size-3.5 transition-transform", details && "rotate-180")} />
        </button>
      </div>

      {details && (
        <dl className="grid gap-2 border-t border-white/6 pt-3 text-xs sm:grid-cols-2">
          <div>
            <dt className="text-muted-foreground">Token expires</dt>
            <dd>{account.token_expires_at ? `${formatDateTime(account.token_expires_at, tz)} (${formatRelative(account.token_expires_at)})` : "Not reported"}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Last checked</dt>
            <dd>{account.last_checked_at ? formatRelative(account.last_checked_at) : "Not checked yet"}</dd>
          </div>
          {account.platform === "youtube" && (
            <>
              <div>
                <dt className="text-muted-foreground">Analytics access</dt>
                <dd>{account.has_analytics_scope ? "Granted" : "Not granted"}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Google Drive access</dt>
                <dd>{account.has_drive_scope ? "Granted (read-only)" : "Not granted"}</dd>
              </div>
            </>
          )}
          <div className="sm:col-span-2">
            <dt className="mb-1 text-muted-foreground">Granted permissions</dt>
            <dd className="flex flex-wrap gap-1">
              {account.scopes.length
                ? account.scopes.map((s) => (
                    <code key={s} title={s} className="rounded bg-white/6 px-1.5 py-0.5 font-mono text-[11px]">
                      {shortScope(s)}
                    </code>
                  ))
                : "None reported"}
            </dd>
          </div>
        </dl>
      )}

      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={`Disconnect ${account.display_name}?`}
        description="ClipFusion deletes its stored access tokens for this account. Posts that are already published stay on the platform."
        confirmLabel="Disconnect"
        destructive
        pending={disconnect.isPending}
        onConfirm={() =>
          disconnect.mutate(account.id, {
            onSuccess: () => {
              setConfirm(false);
              toast.success("Account disconnected");
            },
          })
        }
      />
    </li>
  );
}
