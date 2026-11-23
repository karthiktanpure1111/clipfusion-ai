import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api/client", () => {
  class ApiError extends Error {
    constructor(
      readonly status: number,
      readonly code: string,
      message: string,
    ) {
      super(message);
    }
  }
  return { ApiError, api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() }, uploadBinary: vi.fn() };
});

import { api, ApiError, uploadBinary } from "@/lib/api/client";
import { ChunkedUpload, listPendingUploads } from "./upload";

const get = vi.mocked(api.get);
const post = vi.mocked(api.post);
const del = vi.mocked(api.delete);
const put = vi.mocked(uploadBinary);

const file = new File([new Uint8Array(25)], "stream.mp4", { type: "video/mp4", lastModified: 1 });
const uploadOut = (received: number[] = []) => ({ upload_id: "u1", filename: file.name, size: 25, chunk_size: 10, total_chunks: 3, received_chunks: received, status: "active", expires_at: "" });
const body = { rights_confirmed: true as const };
const chunkIndexes = () => put.mock.calls.map(([path]) => Number(path.split("/").pop()));

/** A chunk request that only ends when it is aborted. */
const hangUntilAborted = (signal?: AbortSignal) =>
  new Promise<never>((_, reject) => signal?.addEventListener("abort", () => reject(new ApiError(0, "aborted", "Upload cancelled"))));

beforeEach(() => {
  const store = new Map<string, string>();
  vi.stubGlobal("window", { localStorage: { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) } });
  post.mockImplementation(async (path: string) => (path.endsWith("/init") ? uploadOut() : { id: "source-1" }));
  put.mockResolvedValue(undefined);
  del.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.resetAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("ChunkedUpload", () => {
  it("uploads exact chunk lengths, completes and forgets the pending upload", async () => {
    const up = new ChunkedUpload(file, body);
    const source = await up.start();
    expect(put.mock.calls.map(([, blob]) => blob.size)).toEqual([10, 10, 5]);
    expect(source).toEqual({ id: "source-1" });
    expect(post).toHaveBeenLastCalledWith("/uploads/u1/complete", body);
    expect(up.snapshot).toMatchObject({ state: "done", loaded: 25 });
    expect(listPendingUploads()).toEqual([]);
  });

  it("pauses, remembers the upload, and resumes with only the missing chunks", async () => {
    put.mockResolvedValueOnce(undefined).mockImplementationOnce((_p, _b, _o, signal) => hangUntilAborted(signal));
    const up = new ChunkedUpload(file, body);
    const started = up.start();
    await vi.waitFor(() => expect(put).toHaveBeenCalledTimes(2));
    up.pause();
    expect(await started).toBeNull();
    expect(up.snapshot.state).toBe("paused");
    expect(listPendingUploads()).toHaveLength(1);

    get.mockResolvedValue(uploadOut([0]));
    await up.resume();
    expect(chunkIndexes()).toEqual([0, 1, 1, 2]);
    expect(up.snapshot.state).toBe("done");
  });

  it("continues a remembered upload after a reload", async () => {
    put.mockRejectedValueOnce(new ApiError(400, "validation_failed", "nope"));
    await new ChunkedUpload(file, body).start();
    put.mockClear();
    get.mockResolvedValue(uploadOut([0, 1]));
    const again = new ChunkedUpload(file, body);
    await again.start();
    expect(post.mock.calls.filter(([p]) => p === "/uploads/init")).toHaveLength(1);
    expect(chunkIndexes()).toEqual([2]);
    expect(again.snapshot.resumed).toBe(true);
  });

  it("retries network errors but stops on client errors", async () => {
    vi.useFakeTimers();
    put.mockRejectedValueOnce(new ApiError(0, "network_error", "offline"));
    const started = new ChunkedUpload(file, body).start();
    await vi.runAllTimersAsync();
    expect(await started).toEqual({ id: "source-1" });
    expect(chunkIndexes()).toEqual([0, 0, 1, 2]);

    put.mockReset().mockRejectedValue(new ApiError(413, "too_large", "Too large"));
    const failed = new ChunkedUpload(file, body);
    expect(await failed.start()).toBeNull();
    expect(put).toHaveBeenCalledTimes(1);
    expect(failed.snapshot).toMatchObject({ state: "error", error: "Too large" });
  });

  it("does not keep uploading when paused during a retry back-off", async () => {
    vi.useFakeTimers();
    put.mockRejectedValueOnce(new ApiError(503, "busy", "busy"));
    const up = new ChunkedUpload(file, body);
    const started = up.start();
    await vi.waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    up.pause();
    await vi.runAllTimersAsync();
    expect(await started).toBeNull();
    expect(put).toHaveBeenCalledTimes(1);
    expect(up.snapshot.state).toBe("paused");
  });

  it("cancelling while the upload is being created deletes it and sends nothing", async () => {
    let finishInit: (v: unknown) => void = () => undefined;
    post.mockImplementationOnce(() => new Promise((r) => (finishInit = r)));
    const up = new ChunkedUpload(file, body);
    const started = up.start();
    await up.cancel();
    finishInit(uploadOut());
    expect(await started).toBeNull();
    expect(put).not.toHaveBeenCalled();
    expect(del).toHaveBeenCalledWith("/uploads/u1");
    expect(up.snapshot.state).toBe("cancelled");
    expect(listPendingUploads()).toEqual([]);
  });
});
