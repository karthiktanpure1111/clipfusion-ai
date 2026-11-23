import type { Metadata } from "next";
import { JobsView } from "@/components/processing/jobs-view";

export const metadata: Metadata = { title: "AI Processing" };

export default function ProcessingPage() {
  return <JobsView />;
}
