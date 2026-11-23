/**
 * ClipFusion API client.
 *
 * - Talks to the FastAPI backend at NEXT_PUBLIC_API_URL (default http://localhost:8010/api/v1).
 * - Auth uses httpOnly cookies set by the backend (`cf_access`, `cf_refresh`), so every request
 *   is sent with `credentials: "include"` and the CSRF header `X-Requested-With: ClipFusion`.
 * - A 401 triggers one single-flight refresh (`POST /auth/refresh`) and a retry; if the refresh
 *   fails the `onUnauthorized` handler runs (the app shell redirects to /login).
 * - Errors are thrown as `ApiError` carrying the backend's `{error: {code, message, details}}`.
 */

export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8010/api/v1").replace(/\/$/, "");

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export type QueryValue = string | number | boolean | null | undefined | Array<string | number>;
export type Query = Record<string, QueryValue>;

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  query?: Query;
  /** JSON-serialisable body, FormData, Blob or ArrayBuffer. */
  body?: unknown;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  /** Skip the automatic refresh-and-retry on 401 (used by the auth endpoints themselves). */
  noRefresh?: boolean;
}

let unauthorizedHandler: (() => void) | null = null;

/** Register what happens when the session is gone (e.g. redirect to /login). */
export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export function buildUrl(path: string, query?: Query): string {
  const url = new URL(API_URL + (path.startsWith("/") ? path : `/${path}`));
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null || value === "") continue;
      if (Array.isArray(value)) value.forEach((v) => url.searchParams.append(key, String(v)));
      else url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

async function parseError(res: Response): Promise<ApiError> {
  let code = "http_error";
  let message = res.statusText || `Request failed (${res.status})`;
  let details: unknown;
  try {
    const data = await res.json();
    if (data?.error) {
      code = data.error.code ?? code;
      message = data.error.message ?? message;
      details = data.error.details;
    } else if (data?.detail) {
      message = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    }
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(res.status, code, message, details);
}

let refreshInFlight: Promise<boolean> | null = null;

async function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = fetch(buildUrl("/auth/refresh"), {
      method: "POST",
      credentials: "include",
      headers: { "X-Requested-With": "ClipFusion", "Content-Type": "application/json" },
      body: "{}",
    })
      .then((r) => r.ok)
      .catch(() => false)
      .finally(() => {
        setTimeout(() => (refreshInFlight = null), 0);
      });
  }
  return refreshInFlight;
}

function encodeBody(body: unknown, headers: Record<string, string>): BodyInit | undefined {
  if (body === undefined) return undefined;
  if (body instanceof FormData || body instanceof Blob || body instanceof ArrayBuffer) return body as BodyInit;
  headers["Content-Type"] = headers["Content-Type"] ?? "application/json";
  return JSON.stringify(body);
}

export async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "ClipFusion", Accept: "application/json", ...opts.headers };
  const init: RequestInit = {
    method: opts.method ?? "GET",
    credentials: "include",
    headers,
    body: encodeBody(opts.body, headers),
    signal: opts.signal,
  };
  const url = buildUrl(path, opts.query);
  let res = await fetch(url, init);
  if (res.status === 401 && !opts.noRefresh) {
    if (await refreshSession()) {
      res = await fetch(url, { ...init, body: encodeBody(opts.body, { ...headers }) });
    }
    if (res.status === 401) {
      unauthorizedHandler?.();
    }
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  return (ct.includes("application/json") ? res.json() : res.text()) as Promise<T>;
}

export const api = {
  get: <T>(path: string, query?: Query, signal?: AbortSignal) => request<T>(path, { query, signal }),
  post: <T>(path: string, body?: unknown, query?: Query) => request<T>(path, { method: "POST", body: body ?? {}, query }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: "PUT", body }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: "PATCH", body }),
  delete: <T = void>(path: string, query?: Query) => request<T>(path, { method: "DELETE", query }),
};

/**
 * Upload a binary chunk with progress reporting (fetch has no upload progress, so XHR is used).
 * Retries once through the refresh flow on 401.
 */
export function uploadBinary(
  path: string,
  data: Blob,
  onProgress?: (loaded: number, total: number) => void,
  signal?: AbortSignal,
  method: "PUT" | "POST" = "PUT",
): Promise<unknown> {
  const send = () =>
    new Promise<{ status: number; body: string }>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open(method, buildUrl(path));
      xhr.withCredentials = true;
      xhr.setRequestHeader("X-Requested-With", "ClipFusion");
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.onprogress = (e) => onProgress?.(e.loaded, e.total || data.size);
      xhr.onload = () => resolve({ status: xhr.status, body: xhr.responseText });
      xhr.onerror = () => reject(new ApiError(0, "network_error", "Network error during upload"));
      xhr.onabort = () => reject(new ApiError(0, "aborted", "Upload cancelled"));
      signal?.addEventListener("abort", () => xhr.abort(), { once: true });
      xhr.send(data);
    });

  const toResult = (r: { status: number; body: string }) => {
    let parsed: unknown = undefined;
    try {
      parsed = r.body ? JSON.parse(r.body) : undefined;
    } catch {
      parsed = r.body;
    }
    if (r.status >= 200 && r.status < 300) return parsed;
    const err = (parsed as { error?: { code?: string; message?: string; details?: unknown } })?.error;
    throw new ApiError(r.status, err?.code ?? "http_error", err?.message ?? `Upload failed (${r.status})`, err?.details);
  };

  return send().then(async (r) => {
    if (r.status === 401 && (await refreshSession())) return toResult(await send());
    return toResult(r);
  });
}
