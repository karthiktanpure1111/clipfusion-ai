import type { Metadata } from "next";
import { Suspense } from "react";
import { CardGridSkeleton } from "@/components/common/page";
import { ClipsView } from "@/components/clips/clips-view";

export const metadata: Metadata = { title: "Generated Clips" };

export default function ClipsPage() {
  return (
    <Suspense fallback={<CardGridSkeleton count={8} />}>
      <ClipsView />
    </Suspense>
  );
}
