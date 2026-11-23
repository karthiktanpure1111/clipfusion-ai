"use client";

import { useState } from "react";

/**
 * Every API response re-signs its media URLs, so the same file gets a new URL on each poll. Using
 * it directly would restart a playing <video> (and re-download thumbnails) every few seconds.
 *
 * The file behind a URL: the storage key inside a ClipFusion media token, or the path of an
 * S3/R2 pre-signed URL. Unknown formats fall back to the URL itself (no de-duplication).
 */
export function mediaIdentity(url: string): string {
  try {
    const u = new URL(url);
    const token = /\/media\/([^/]+)$/.exec(u.pathname)?.[1];
    if (!token) return u.origin + u.pathname;
    const payload = token.split(".")[0].replace(/-/g, "+").replace(/_/g, "/");
    const key = (JSON.parse(atob(payload)) as { k?: unknown }).k;
    return typeof key === "string" ? key : url;
  } catch {
    return url;
  }
}

/**
 * Keeps the first URL seen for a file. `version` forces a new URL when the same file was
 * regenerated. `renew` switches to the newest URL (call it when loading fails, e.g. after the
 * old one expired) and reports whether there was a newer one to try.
 */
export function useStableUrl(url: string | null | undefined, version = ""): { url: string | undefined; renew: () => boolean } {
  const id = url ? `${mediaIdentity(url)}|${version}` : "";
  const [held, setHeld] = useState({ id, url });
  if (held.id !== id) setHeld({ id, url }); // adjust during render: a different file is shown now
  const current = (held.id === id ? held.url : url) ?? undefined;
  const renew = () => {
    if (!url || current === url) return false;
    setHeld({ id, url });
    return true;
  };
  return { url: current, renew };
}
