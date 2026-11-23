"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Info, UploadCloud } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { YouTubeIcon } from "@/components/common/brand";
import { PageHeader, Surface } from "@/components/common/page";
import { useMe } from "@/lib/api/hooks/auth";
import type { AnalysisRequest, SourceOut, UserSettings } from "@/lib/api/types";
import { analysisErrors, defaultAnalysis, ProcessingOptions } from "./processing-options";
import { YouTubePanel } from "./source-panels";
import { UploadPanel } from "./upload-panel";

function NewVideoForm({ settings }: { settings?: UserSettings }) {
  const router = useRouter();
  const [tab, setTab] = useState("upload");
  const [analysis, setAnalysis] = useState<AnalysisRequest>(() => defaultAnalysis(settings));
  const blocked = analysisErrors(analysis)[0] ?? null;
  // processing starts automatically on the server; the video page shows the live pipeline
  const open = (s: SourceOut) => router.push(`/app/videos/${s.id}`);

  return (
    <div className="space-y-5">
      <Surface className="p-4 sm:p-6">
        <ProcessingOptions value={analysis} onChange={setAnalysis} idPrefix="new" />
        <p className="mt-3 flex gap-1.5 text-xs text-muted-foreground">
          <Info className="mt-px size-3.5 shrink-0" /> Processing starts automatically as soon as the video is in, using free local AI on this computer. You&apos;ll be taken to the video&apos;s page to follow every stage.
        </p>
      </Surface>
      <Surface className="p-4 sm:p-6">
        <Tabs value={tab} onValueChange={(v) => setTab(String(v))}>
          <TabsList className="mb-5 grid h-11! w-full grid-cols-2 rounded-xl bg-white/4 p-1">
            <TabsTrigger value="upload" className="rounded-lg">
              <UploadCloud /> <span className="hidden sm:inline">Upload</span> file
            </TabsTrigger>
            <TabsTrigger value="youtube" className="rounded-lg">
              <YouTubeIcon /> <span className="hidden sm:inline">YouTube</span> link
            </TabsTrigger>
          </TabsList>
          <TabsContent value="upload" keepMounted>
            <UploadPanel analysis={analysis} blockedReason={blocked} onComplete={open} />
          </TabsContent>
          <TabsContent value="youtube" keepMounted>
            <YouTubePanel analysis={analysis} blockedReason={blocked} onReady={open} />
          </TabsContent>
        </Tabs>
      </Surface>
    </div>
  );
}

export function NewVideoView() {
  const me = useMe();
  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        title="Add New Video"
        description="Bring in a long video — a stream recording, podcast, interview or gameplay session — and ClipFusion cuts its best moments into vertical clips."
      />
      {me.isLoading ? <Skeleton className="h-[480px] rounded-2xl" /> : <NewVideoForm settings={me.data?.settings} />}
    </div>
  );
}
