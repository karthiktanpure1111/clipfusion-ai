import type { Metadata } from "next";
import { VideosView } from "@/components/videos/videos-view";

export const metadata: Metadata = { title: "Videos" };

export default function VideosPage() {
  return <VideosView />;
}
