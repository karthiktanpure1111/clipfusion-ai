import type { Metadata } from "next";
import { Suspense } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { ClipDetailView } from "@/components/clips/clip-detail";

export const metadata: Metadata = { title: "Clip" };

export default function ClipDetailPage() {
  return (
    <Suspense fallback={<Skeleton className="aspect-video max-w-3xl rounded-2xl" />}>
      <ClipDetailView />
    </Suspense>
  );
}
