"use client";

import { useCallback, useRef, useState, useSyncExternalStore } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { FileVideo, History, Loader2, Pause, Play, UploadCloud, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RightsCheckbox } from "@/components/common/fields";
import { JobProgressBar } from "@/components/processing/job-card";
import { useQueryClient } from "@tanstack/react-query";
import { qk } from "@/lib/api/hooks/common";
import type { AnalysisRequest, SourceOut } from "@/lib/api/types";
import { formatBytes, formatDuration, formatSpeed } from "@/lib/format";
import { ChunkedUpload, forgetPendingUpload, listPendingUploads, matchesFile, type UploadSnapshot } from "@/lib/upload";
import { cn } from "@/lib/utils";

const noopSubscribe = () => () => undefined;

export function UploadPanel({
  videoSourceId,
  onComplete,
  compact = false,
  analysis,
  blockedReason,
  rightsConfirmed = false,
}: {
  videoSourceId?: string;
  onComplete: (source: SourceOut) => void;
  compact?: boolean;
  /** Processing options: when given, processing starts automatically once the upload completes. */
  analysis?: AnalysisRequest;
  /** Why the upload can't start yet (e.g. invalid processing options). */
  blockedReason?: string | null;
  /** The rights statement was already confirmed for this video (e.g. when fetching YouTube details). */
  rightsConfirmed?: boolean;
}) {
  const qc = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [rights, setRights] = useState(rightsConfirmed);
  const [dragging, setDragging] = useState(false);
  const [upload, setUpload] = useState<ChunkedUpload | null>(null);
  const [pending, setPending] = useState(listPendingUploads);

  const subscribe = useCallback(
    (cb: () => void) => {
      if (!upload) return noopSubscribe();
      const unsub = upload.subscribe(() => cb());
      return () => void unsub();
    },
    [upload],
  );
  const snap: UploadSnapshot | null = useSyncExternalStore(
    subscribe,
    () => upload?.snapshot ?? null,
    () => null,
  );

  const pick = (f: File | null | undefined) => {
    if (!f) return;
    if (f.type && !f.type.startsWith("video/")) {
      toast.error("That doesn't look like a video file.");
      return;
    }
    setFile(f);
    if (!title) setTitle(f.name.replace(/\.[^.]+$/, ""));
  };

  const reset = () => {
    setUpload(null);
    setFile(null);
    setTitle("");
    setPending(listPendingUploads());
  };

  const finish = (source: SourceOut) => {
    qc.invalidateQueries({ queryKey: ["sources"] });
    qc.invalidateQueries({ queryKey: qk.source(source.id) });
    qc.invalidateQueries({ queryKey: qk.dashboard });
    if (source.duplicate) toast.info("This file is already in your library — using the existing video.");
    else toast.success(source.active_job ? "Upload complete — processing started" : "Upload complete");
    reset();
    setRights(rightsConfirmed);
    onComplete(source);
  };

  const start = async () => {
    if (!file || !rights) return;
    const up = new ChunkedUpload(file, {
      title: title.trim() || undefined,
      rights_confirmed: true,
      video_source_id: videoSourceId,
      auto_analyze: !!analysis,
      ...(analysis ? { analysis } : {}),
    });
    setUpload(up);
    const source = await up.start();
    setPending(listPendingUploads());
    if (source) finish(source);
    else if (up.snapshot.state === "error") toast.error(up.snapshot.error ?? "Upload failed");
  };

  const busy = snap && ["preparing", "uploading", "paused", "completing", "error"].includes(snap.state);
  const pct = snap ? Math.round((snap.loaded / Math.max(1, snap.total)) * 100) : 0;
  const resumable = file ? pending.find((p) => matchesFile(p, file)) : undefined;

  if (busy && snap && file) {
    return (
      <div className="space-y-4 rounded-2xl border border-white/8 bg-black/20 p-5">
        <div className="flex items-center gap-3">
          <span className="grid size-10 place-items-center rounded-xl bg-ig-purple/15 ring-1 ring-ig-purple/30">
            <FileVideo className="size-5" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate font-medium">{file.name}</p>
            <p className="text-xs text-muted-foreground">
              {formatBytes(snap.loaded)} of {formatBytes(snap.total)}
              {snap.resumed && " · resumed"}
            </p>
          </div>
          <span className="font-heading text-xl font-bold tabular-nums">{pct}%</span>
        </div>
        <JobProgressBar value={pct / 100} status={snap.state === "error" ? "failed" : "running"} />
        <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
          <span aria-live="polite">
            {snap.state === "preparing" && "Preparing upload…"}
            {snap.state === "uploading" && `${formatSpeed(snap.speed)} · ${snap.eta != null ? `${formatDuration(snap.eta)} left` : "estimating…"}`}
            {snap.state === "paused" && "Paused — you can resume any time, even after reloading the page."}
            {snap.state === "completing" && "Verifying and finalising the file…"}
            {snap.state === "error" && <span className="text-[#ff5c7a]">{snap.error}</span>}
          </span>
          <div className="flex gap-2">
            {snap.state === "uploading" && (
              <Button variant="outline" size="sm" onClick={() => upload?.pause()}>
                <Pause /> Pause
              </Button>
            )}
            {(snap.state === "paused" || snap.state === "error") && (
              <Button
                size="sm"
                onClick={async () => {
                  const s = await upload?.resume();
                  if (s) finish(s);
                }}
              >
                <Play /> Resume
              </Button>
            )}
            {snap.state !== "completing" && (
              <Button
                variant="ghost"
                size="sm"
                onClick={async () => {
                  await upload?.cancel();
                  reset();
                }}
              >
                <X /> Cancel
              </Button>
            )}
            {snap.state === "completing" && <Loader2 className="size-4 animate-spin" />}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <AnimatePresence>
        {pending.length > 0 && !file && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="rounded-xl border border-info/25 bg-info/5 p-3 text-sm">
            <p className="flex items-center gap-2 font-medium">
              <History className="size-4 text-info" /> Unfinished uploads
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground">Select the same file again to continue where it stopped.</p>
            <ul className="mt-2 space-y-1">
              {pending.map((p) => (
                <li key={p.upload_id} className="flex items-center justify-between gap-2 text-xs">
                  <span className="truncate">
                    {p.name} <span className="text-muted-foreground">({formatBytes(p.size)})</span>
                  </span>
                  <button
                    type="button"
                    className="shrink-0 text-muted-foreground hover:text-foreground"
                    onClick={() => {
                      forgetPendingUpload(p.upload_id);
                      setPending(listPendingUploads());
                    }}
                  >
                    Forget
                  </button>
                </li>
              ))}
            </ul>
          </motion.div>
        )}
      </AnimatePresence>

      <div
        role="button"
        tabIndex={0}
        aria-label="Choose a video file to upload, or drop it here"
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          pick(e.dataTransfer.files?.[0]);
        }}
        className={cn(
          "group relative flex cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed text-center transition-all outline-none focus-visible:ring-2 focus-visible:ring-ring",
          compact ? "px-4 py-8" : "px-6 py-14",
          dragging ? "border-ig-pink/70 bg-ig-pink/5" : "border-white/12 bg-white/2 hover:border-white/25 hover:bg-white/4",
        )}
      >
        <input ref={inputRef} type="file" accept="video/*" className="hidden" onChange={(e) => {
            pick(e.target.files?.[0]);
            e.target.value = ""; // allow choosing the same file again
          }}
        />
        <motion.span animate={dragging ? { scale: 1.1, y: -4 } : { scale: 1, y: 0 }} className="gradient-border mb-4 grid size-14 place-items-center rounded-2xl bg-black/30">
          <UploadCloud className="size-6" />
        </motion.span>
        {file ? (
          <>
            <p className="font-medium">{file.name}</p>
            <p className="mt-1 text-sm text-muted-foreground">
              {formatBytes(file.size)} · click to choose another file
              {resumable && <span className="ml-1 text-info">· will resume previous upload</span>}
            </p>
          </>
        ) : (
          <>
            <p className="font-medium">Drag &amp; drop the video file</p>
            <p className="mt-1 text-sm text-muted-foreground">or click to browse · MP4, MOV, MKV, WebM · large files upload in resumable chunks</p>
          </>
        )}
      </div>

      {file && !videoSourceId && (
        <div className="space-y-1.5">
          <Label htmlFor="upload-title">Title</Label>
          <Input id="upload-title" value={title} onChange={(e) => setTitle(e.target.value)} className="h-9 rounded-xl" maxLength={200} />
        </div>
      )}
      {!rightsConfirmed && <RightsCheckbox id={videoSourceId ? `rights-attach-${videoSourceId}` : "rights-upload"} checked={rights} onChange={setRights} />}
      {blockedReason && file && rights && <p className="text-xs text-[#ff5c7a]">{blockedReason}</p>}
      <Button size="lg" className="h-10 w-full rounded-xl sm:w-auto" disabled={!file || !rights || !!blockedReason} onClick={start}>
        <UploadCloud /> {videoSourceId ? "Upload & attach the creator's file" : analysis ? "Upload & start processing" : "Start upload"}
      </Button>
    </div>
  );
}
