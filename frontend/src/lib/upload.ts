/**
 * Resumable chunked upload against POST /uploads/init + PUT /uploads/{id}/chunks/{index}.
 * Pending uploads are remembered in localStorage ({upload_id, name, size, lastModified}) so a
 * reload can resume once the user re-selects the same file.
 */
import { api, ApiError, uploadBinary } from "@/lib/api/client";
import type { AnalysisRequest, ChunkOut, SourceOut, UploadOut } from "@/lib/api/types";

const STORAGE_KEY = "clipfusion.pendingUploads";

export interface PendingUpload {
  upload_id: string;
  name: string;
  size: number;
  lastModified: number;
  video_source_id?: string | null;
  created_at: number;
}

function readPending(): PendingUpload[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const parsed = raw ? (JSON.parse(raw) as PendingUpload[]) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writePending(list: PendingUpload[]) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
  } catch {
    /* storage unavailable (private mode) — resume just won't survive reloads */
  }
}

export function listPendingUploads(): PendingUpload[] {
  if (typeof window === "undefined") return [];
  return readPending();
}

export function forgetPendingUpload(uploadId: string) {
  writePending(readPending().filter((p) => p.upload_id !== uploadId));
}

function rememberPending(p: PendingUpload) {
  writePending([...readPending().filter((x) => x.upload_id !== p.upload_id), p]);
}

export const matchesFile = (p: PendingUpload, f: File) =>
  p.name === f.name && p.size === f.size && p.lastModified === f.lastModified;

export type UploadState = "idle" | "preparing" | "uploading" | "paused" | "completing" | "done" | "error" | "cancelled";

export interface UploadSnapshot {
  state: UploadState;
  loaded: number;
  total: number;
  speed: number; // bytes/s
  eta: number | null; // seconds
  resumed: boolean;
  error: string | null;
  source: SourceOut | null;
}

export interface CompleteBody {
  title?: string;
  rights_confirmed: true;
  rights_statement?: string;
  video_source_id?: string;
  attribution?: string;
  /** Start processing as soon as the file is complete (API default true). */
  auto_analyze?: boolean;
  analysis?: AnalysisRequest;
}

export class ChunkedUpload {
  private upload: UploadOut | null = null;
  private received = new Set<number>();
  private controller: AbortController | null = null;
  private samples: { t: number; loaded: number }[] = [];
  private listeners = new Set<(s: UploadSnapshot) => void>();
  snapshot: UploadSnapshot;

  constructor(
    readonly file: File,
    private readonly completeBody: CompleteBody,
  ) {
    this.snapshot = { state: "idle", loaded: 0, total: file.size, speed: 0, eta: null, resumed: false, error: null, source: null };
  }

  subscribe(fn: (s: UploadSnapshot) => void) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit(patch: Partial<UploadSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((l) => l(this.snapshot));
  }

  private receivedBytes(): number {
    if (!this.upload) return 0;
    const { chunk_size, size, total_chunks } = this.upload;
    let bytes = 0;
    this.received.forEach((i) => {
      bytes += i === total_chunks - 1 ? size - chunk_size * i : chunk_size;
    });
    return bytes;
  }

  private track(loaded: number) {
    const now = performance.now();
    this.samples.push({ t: now, loaded });
    while (this.samples.length > 2 && now - this.samples[0].t > 5000) this.samples.shift();
    const first = this.samples[0];
    const dt = (now - first.t) / 1000;
    const speed = dt > 0.3 ? Math.max(0, (loaded - first.loaded) / dt) : this.snapshot.speed;
    const eta = speed > 0 ? (this.file.size - loaded) / speed : null;
    this.emit({ loaded, speed, eta });
  }

  /** Init a new upload or resume a remembered one, then upload the missing chunks. */
  async start(): Promise<SourceOut | null> {
    this.emit({ state: "preparing", error: null });
    try {
      if (!this.upload) await this.initOrResume();
      if (this.isCancelled()) {
        await this.discard(); // cancelled while the upload was being created
        return null;
      }
      return await this.run();
    } catch (e) {
      return this.fail(e);
    }
  }

  private async initOrResume() {
    const existing = listPendingUploads().find((p) => matchesFile(p, this.file));
    if (existing) {
      try {
        const up = await api.get<UploadOut>(`/uploads/${existing.upload_id}`);
        if (up.size === this.file.size && !["completed", "expired", "cancelled"].includes(up.status)) {
          this.upload = up;
          this.received = new Set(up.received_chunks);
          this.emit({ resumed: up.received_chunks.length > 0 });
          return;
        }
      } catch {
        /* expired or gone — start fresh */
      }
      forgetPendingUpload(existing.upload_id);
    }
    this.upload = await api.post<UploadOut>("/uploads/init", {
      filename: this.file.name,
      size: this.file.size,
      mime_type: this.file.type || undefined,
    });
    rememberPending({
      upload_id: this.upload.upload_id,
      name: this.file.name,
      size: this.file.size,
      lastModified: this.file.lastModified,
      video_source_id: this.completeBody.video_source_id ?? null,
      created_at: Date.now(),
    });
  }

  private async run(): Promise<SourceOut | null> {
    const up = this.upload!;
    this.controller = new AbortController();
    const signal = this.controller.signal;
    this.samples = [];
    this.emit({ state: "uploading" });
    this.track(this.receivedBytes());

    for (let i = 0; i < up.total_chunks; i++) {
      if (this.received.has(i)) continue;
      if (signal.aborted) return null;
      const blob = this.file.slice(i * up.chunk_size, Math.min(up.size, (i + 1) * up.chunk_size));
      const base = this.receivedBytes();
      let attempt = 0;
      for (;;) {
        try {
          const res = (await uploadBinary(
            `/uploads/${up.upload_id}/chunks/${i}`,
            blob,
            (loaded) => this.track(base + loaded),
            signal,
          )) as ChunkOut | undefined;
          if (res?.received_chunks) this.received = new Set(res.received_chunks);
          this.received.add(i);
          break;
        } catch (e) {
          if (signal.aborted || (e instanceof ApiError && e.code === "aborted")) return null;
          const retriable = e instanceof ApiError && (e.status === 0 || e.status >= 500 || e.status === 429);
          if (!retriable || attempt >= 3) throw e;
          attempt++;
          await new Promise((r) => setTimeout(r, 800 * 2 ** attempt));
          if (signal.aborted) return null; // paused or cancelled during the back-off
        }
      }
      this.track(this.receivedBytes());
    }

    this.emit({ state: "completing", eta: 0 });
    const source = await api.post<SourceOut>(`/uploads/${up.upload_id}/complete`, this.completeBody);
    forgetPendingUpload(up.upload_id);
    this.emit({ state: "done", source, loaded: this.file.size });
    return source;
  }

  private fail(e: unknown): null {
    if (this.snapshot.state === "paused" || this.snapshot.state === "cancelled") return null;
    const msg = e instanceof Error ? e.message : "Upload failed";
    this.emit({ state: "error", error: msg });
    return null;
  }

  pause() {
    if (this.snapshot.state !== "uploading") return;
    this.emit({ state: "paused", speed: 0, eta: null });
    this.controller?.abort();
  }

  async resume(): Promise<SourceOut | null> {
    if (this.snapshot.state !== "paused" && this.snapshot.state !== "error") return null;
    try {
      if (this.upload) {
        const fresh = await api.get<UploadOut>(`/uploads/${this.upload.upload_id}`);
        this.received = new Set(fresh.received_chunks);
        return await this.run();
      }
      return await this.start();
    } catch (e) {
      return this.fail(e);
    }
  }

  async cancel() {
    this.emit({ state: "cancelled", speed: 0, eta: null });
    this.controller?.abort();
    await this.discard();
  }

  private isCancelled(): boolean {
    return this.snapshot.state === "cancelled";
  }

  private async discard() {
    if (!this.upload) return;
    forgetPendingUpload(this.upload.upload_id);
    try {
      await api.delete(`/uploads/${this.upload.upload_id}`);
    } catch {
      /* already gone */
    }
  }
}
