import type { Metadata } from "next";

import { FramingEntry } from "@/components/framing/FramingEntry";

export const metadata: Metadata = { title: "Smart Framing" };

export default function FramingEntryPage() {
  return <FramingEntry />;
}
