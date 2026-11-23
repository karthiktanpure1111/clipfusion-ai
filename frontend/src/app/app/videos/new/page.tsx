import type { Metadata } from "next";
import { ClientOnly } from "@/components/common/client-only";
import { Skeleton } from "@/components/ui/skeleton";
import { NewVideoView } from "@/components/videos/new-video-view";

export const metadata: Metadata = { title: "Add New Video" };

export default function NewVideoPage() {
  return (
    <ClientOnly fallback={<Skeleton className="h-[480px] rounded-2xl" />}>
      <NewVideoView />
    </ClientOnly>
  );
}
