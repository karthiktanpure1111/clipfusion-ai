/**
 * Client-side validation of YouTube video links: youtube.com/watch?v=ID, youtu.be/ID,
 * youtube.com/shorts/ID and youtube.com/live/ID (www., m. and music. hosts allowed, scheme optional).
 * Returns the 11-character video id, or null when the text is not one of these links.
 */
const ID = /^[A-Za-z0-9_-]{11}$/;
const HOSTS = new Set(["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"]);

export function parseYouTubeUrl(input: string): string | null {
  const raw = input.trim();
  if (!raw || /\s/.test(raw)) return null;
  let url: URL;
  try {
    url = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(raw) ? raw : `https://${raw}`);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  const host = url.hostname.toLowerCase();
  const parts = url.pathname.split("/").filter(Boolean);
  let id: string | null = null;
  if (host === "youtu.be" || host === "www.youtu.be") {
    id = parts.length === 1 ? parts[0] : null;
  } else if (HOSTS.has(host)) {
    if (parts.length === 1 && parts[0] === "watch") id = url.searchParams.get("v");
    else if (parts.length === 2 && (parts[0] === "shorts" || parts[0] === "live")) id = parts[1];
  }
  return id && ID.test(id) ? id : null;
}

export const YOUTUBE_URL_HINT = "Use a youtube.com/watch?v=…, youtu.be/…, youtube.com/shorts/… or youtube.com/live/… link.";
