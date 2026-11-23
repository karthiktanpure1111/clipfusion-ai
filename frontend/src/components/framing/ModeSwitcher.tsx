"use client";

import { motion } from "framer-motion";
import { Crop, Expand, Gamepad2, PictureInPicture2, ScanFace } from "lucide-react";
import { cn } from "@/lib/utils";

import type { FramingMode } from "@/lib/framing/types";

export const MODES: { mode: FramingMode; title: string; description: string; icon: typeof Crop }[] = [
  { mode: "smart", title: "AI Smart Crop", description: "Follows the main speaker with a calm virtual camera.", icon: ScanFace },
  { mode: "manual", title: "Manual Crop", description: "Drag and resize exactly what stays in frame.", icon: Crop },
  { mode: "facecam_gameplay", title: "Facecam + Gameplay", description: "Stacked facecam panel over the gameplay.", icon: Gamepad2 },
  { mode: "pip", title: "Picture-in-Picture", description: "Full video with a movable facecam overlay.", icon: PictureInPicture2 },
  { mode: "full_frame", title: "Full Frame / No Crop", description: "Keeps everything, fitted on a blurred background.", icon: Expand },
];

export function ModeSwitcher({
  value,
  suggested,
  onChange,
}: {
  value: FramingMode;
  suggested?: FramingMode | null;
  onChange: (m: FramingMode) => void;
}) {
  return (
    <div role="radiogroup" aria-label="Framing mode" className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-5">
      {MODES.map(({ mode, title, description, icon: Icon }, i) => {
        const active = value === mode;
        return (
          <button
            key={mode}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(mode)}
            title={`${title} (${i + 1})`}
            className={cn(
              "group relative flex items-start gap-2.5 overflow-hidden rounded-2xl border border-white/[0.06] bg-card/70 p-3 text-left transition outline-none hover:border-white/15 focus-visible:ring-2 focus-visible:ring-ring",
              active && "border-transparent",
            )}
          >
            {active && (
              <motion.span
                layoutId="framing-mode-active"
                className="pointer-events-none absolute inset-0 rounded-2xl"
                transition={{ type: "spring", stiffness: 420, damping: 34 }}
              >
                {/* nested: .gradient-border sets position: relative and would undo `absolute` */}
                <span className="gradient-border block size-full rounded-2xl" />
              </motion.span>
            )}
            <span
              className={cn(
                "grid size-8 shrink-0 place-items-center rounded-xl bg-muted text-muted-foreground transition",
                active && "bg-ig-gradient text-white shadow-lg",
              )}
            >
              <Icon className="size-4" />
            </span>
            <span className="min-w-0">
              <span className="flex items-center gap-1.5 text-[13px] font-semibold">
                {title}
                {suggested === mode && (
                  <span className="rounded-full bg-ig-purple/30 px-1.5 text-[9px] font-semibold text-[#d9b6ff] uppercase">AI pick</span>
                )}
              </span>
              <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground">{description}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}
