import type { Metadata } from "next";
import { Suspense } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { VideoDetailView } from "@/components/videos/video-detail";

export const metadata: Metadata = { title: "Video" };

export default function VideoDetailPage() {
  return (
    <Suspense fallback={<Skeleton className="aspect-video max-w-3xl rounded-2xl" />}>
      <VideoDetailView />
    </Suspense>
  );
}
