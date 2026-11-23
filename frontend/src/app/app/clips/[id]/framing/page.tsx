import type { Metadata } from "next";
import { Suspense } from "react";

import { EditorSkeleton, FramingEditor } from "@/components/framing/FramingEditor";

export const metadata: Metadata = { title: "Smart Framing Editor" };

export default function FramingPage({ params }: { params: Promise<{ id: string }> }) {
  return (
    <Suspense fallback={<EditorSkeleton />}>
      <FramingEditor params={params} />
    </Suspense>
  );
}
