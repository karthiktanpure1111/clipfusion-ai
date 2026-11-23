"use client";

import { CheckCircle2, Copy, ExternalLink, KeyRound } from "lucide-react";
import { toast } from "sonner";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import type { PlatformRequirements } from "@/lib/api/types";
import { formatCount } from "@/lib/format";

function Bullets({ items }: { items: string[] }) {
  return (
    <ul className="space-y-1.5 text-sm text-muted-foreground">
      {items.map((i) => (
        <li key={i} className="flex gap-2">
          <span className="mt-2 size-1 shrink-0 rounded-full bg-ig-gradient" aria-hidden /> {i}
        </li>
      ))}
    </ul>
  );
}

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success("Copied");
  } catch {
    toast.error("Couldn't copy — select the text and copy it manually.");
  }
}

/** What this integration needs (from GET /accounts/requirements), rendered as an expandable checklist. */
export function RequirementsPanel({ req }: { req: PlatformRequirements }) {
  const links = req.docs_links.filter((l) => /^https?:\/\//.test(l.url));
  const localDocs = req.docs_links.filter((l) => !/^https?:\/\//.test(l.url));
  return (
    <div className="space-y-3">
      {req.configured ? (
        <p className="flex items-center gap-2 text-sm text-success">
          <CheckCircle2 className="size-4" /> API credentials are configured on this server
        </p>
      ) : (
        <div role="status" className="space-y-2 rounded-xl border border-warning/30 bg-warning/5 p-3.5 text-sm">
          <p className="flex items-center gap-2 font-medium text-warning">
            <KeyRound className="size-4" /> Not configured on this server yet
          </p>
          <p className="text-xs text-muted-foreground">Add these values to the backend .env file and restart it:</p>
          <div className="flex flex-wrap gap-1.5">
            {req.missing_env.map((e) => (
              <code key={e} className="rounded bg-black/40 px-1.5 py-0.5 font-mono text-xs">
                {e}
              </code>
            ))}
          </div>
        </div>
      )}

      <Accordion>
        <AccordionItem value="setup">
          <AccordionTrigger>Setup: redirect URIs and permissions</AccordionTrigger>
          <AccordionContent className="space-y-4">
            <div>
              <p className="mb-1.5 text-xs font-medium">Register these redirect URIs with the provider</p>
              {req.redirect_uris.map((u) => (
                <div key={u} className="flex items-center gap-2 rounded-lg bg-black/30 py-1 pr-1 pl-2.5">
                  <code className="min-w-0 flex-1 font-mono text-xs break-all">{u}</code>
                  <Button variant="ghost" size="icon-sm" aria-label="Copy redirect URI" onClick={() => copy(u)}>
                    <Copy />
                  </Button>
                </div>
              ))}
            </div>
            <ul className="space-y-2">
              {req.scopes.map((s) => (
                <li key={s.scope} className="text-xs">
                  <code className="font-mono break-all">{s.scope}</code>
                  {s.classification && <span className="ml-1.5 rounded bg-white/6 px-1.5 text-[10px] text-muted-foreground">{s.classification}</span>}
                  {s.required === false && <span className="ml-1.5 rounded bg-white/6 px-1.5 text-[10px] text-muted-foreground">optional</span>}
                  <span className="block text-muted-foreground">{s.purpose}</span>
                </li>
              ))}
            </ul>
          </AccordionContent>
        </AccordionItem>
        <AccordionItem value="eligibility">
          <AccordionTrigger>Account eligibility</AccordionTrigger>
          <AccordionContent>
            <Bullets items={req.eligibility} />
          </AccordionContent>
        </AccordionItem>
        <AccordionItem value="review">
          <AccordionTrigger>App review and limits</AccordionTrigger>
          <AccordionContent className="space-y-3">
            <Bullets items={req.app_review} />
            {req.quota && (
              <p className="text-xs text-muted-foreground">
                Quota: {formatCount(req.quota.daily_units)} units per day, about {formatCount(req.quota.upload_cost)} per upload.
              </p>
            )}
            {req.publish_limit != null && <p className="text-xs text-muted-foreground">ClipFusion limit: {req.publish_limit} posts per day per account.</p>}
          </AccordionContent>
        </AccordionItem>
        <AccordionItem value="notes">
          <AccordionTrigger>Good to know</AccordionTrigger>
          <AccordionContent>
            <Bullets items={req.notes} />
          </AccordionContent>
        </AccordionItem>
      </Accordion>

      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {links.map((l) => (
          <a key={l.url} href={l.url} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-muted-foreground underline-offset-4 hover:text-foreground hover:underline">
            {l.title} <ExternalLink className="size-3" />
          </a>
        ))}
        {localDocs.map((l) => (
          <span key={l.url} className="text-muted-foreground">
            {l.title}: <code className="font-mono">{l.url}</code>
          </span>
        ))}
      </div>
    </div>
  );
}
