"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { AlertTriangle, CalendarClock, CheckCircle2, Clapperboard, Clock4, Cpu, Library, UploadCloud } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Countdown } from "@/components/common/fields";
import { itemVariants, listVariants } from "@/components/common/page";
import { useUserTz } from "@/lib/api/hooks/auth";
import { useStorage } from "@/lib/api/hooks/sources";
import type { DashboardSummary } from "@/lib/api/types";
import { formatCount, formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

interface Stat {
  label: string;
  value: React.ReactNode;
  hint: React.ReactNode;
  icon: LucideIcon;
  href: string;
  glow: string;
  iconClass: string;
}

export function StatCards({ data }: { data: DashboardSummary }) {
  const tz = useUserTz();
  const storage = useStorage();
  // generated = rendered MP4 ready and not uploaded yet
  const ready = data.ready_to_upload ?? storage.data?.clips.generated;
  const stats: Stat[] = [
    { label: "Total videos", value: formatCount(data.total_videos), hint: "Sources in your library", icon: Library, href: "/app/videos", glow: "from-[#515BD4]/25", iconClass: "text-[#8f96ff]" },
    { label: "Generated clips", value: formatCount(data.total_clips), hint: "Across all videos", icon: Clapperboard, href: "/app/clips", glow: "from-[#8134AF]/25", iconClass: "text-[#c79cf0]" },
    { label: "Processing videos", value: formatCount(data.processing_videos), hint: "Processing in progress", icon: Cpu, href: "/app/processing", glow: "from-[#DD2A7B]/25", iconClass: "text-[#f07ab4]" },
    { label: "Ready to upload", value: ready == null ? "–" : formatCount(ready), hint: "Generated, not uploaded yet", icon: UploadCloud, href: "/app/clips?status=generated", glow: "from-[#FCAF45]/20", iconClass: "text-[#FCAF45]" },
    { label: "Scheduled posts", value: formatCount(data.scheduled_posts), hint: "In the publishing queue", icon: CalendarClock, href: "/app/calendar", glow: "from-[#38BDF8]/20", iconClass: "text-info" },
    { label: "Published clips", value: formatCount(data.published_clips), hint: "Successfully posted", icon: CheckCircle2, href: "/app/analytics", glow: "from-[#22C55E]/20", iconClass: "text-success" },
    {
      label: "Failed uploads",
      value: formatCount(data.failed_uploads),
      hint: data.failed_uploads ? "Needs attention" : "All good",
      icon: AlertTriangle,
      href: "/app/calendar",
      glow: data.failed_uploads ? "from-[#FF0033]/30" : "from-white/5",
      iconClass: data.failed_uploads ? "text-[#ff5c7a]" : "text-muted-foreground",
    },
    {
      label: "Next publishing time",
      value: data.next_publish_at ? <Countdown to={data.next_publish_at} /> : "—",
      hint: data.next_publish_at ? formatDateTime(data.next_publish_at, tz) : "Nothing scheduled",
      icon: Clock4,
      href: "/app/calendar",
      glow: "from-[#FF0033]/25",
      iconClass: "text-[#ff4d6d]",
    },
  ];

  return (
    <motion.ul variants={listVariants} initial="hidden" animate="show" className="grid grid-cols-2 gap-3 lg:grid-cols-4 lg:gap-4">
      {stats.map((s) => (
        <motion.li key={s.label} variants={itemVariants} whileHover={{ y: -3 }}>
          <Link
            href={s.href}
            className={cn(
              "group relative flex h-full flex-col overflow-hidden rounded-2xl border border-white/7 bg-card/70 p-4 outline-none transition-colors hover:border-white/15 focus-visible:ring-2 focus-visible:ring-ring sm:p-5",
              "bg-gradient-to-br to-transparent to-70%",
              s.glow,
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-muted-foreground sm:text-[13px]">{s.label}</span>
              <span className="grid size-8 shrink-0 place-items-center rounded-xl bg-black/25 ring-1 ring-white/10">
                <s.icon className={cn("size-4", s.iconClass)} />
              </span>
            </div>
            <div className="mt-3 font-heading text-2xl font-bold tracking-tight tabular-nums sm:text-3xl">{s.value}</div>
            <div className="mt-1 truncate text-xs text-muted-foreground">{s.hint}</div>
          </Link>
        </motion.li>
      ))}
    </motion.ul>
  );
}
