"use client";

import { motion, useReducedMotion } from "framer-motion";
import { ScanFace, Sparkles } from "lucide-react";

/** Illustrative product visual: landscape source -> clean 9:16 facecam + gameplay output. */
export function FramingVisual() {
  const reduce = useReducedMotion();

  return (
    <div className="relative mx-auto w-full max-w-5xl" aria-label="Illustration: a landscape video reframed into a vertical clip with facecam and gameplay" role="img">
      <div aria-hidden className="absolute -inset-10 -z-10 rounded-[48px] bg-[radial-gradient(50%_60%_at_30%_40%,rgb(129_52_175/0.35),transparent),radial-gradient(40%_50%_at_80%_60%,rgb(255_0_51/0.28),transparent)] blur-2xl" />
      <div className="glass grid items-center gap-6 rounded-[28px] p-4 sm:p-6 md:grid-cols-[1.6fr_auto_0.75fr]">
        {/* Landscape source */}
        <div className="relative">
          <div className="mb-2 flex items-center justify-between text-[11px] text-muted-foreground">
            <span>Source · 16:9 stream recording</span>
            <span className="flex items-center gap-1"><ScanFace className="size-3.5" /> face tracked</span>
          </div>
          <div className="relative aspect-video overflow-hidden rounded-2xl ring-1 ring-white/10">
            <Gameplay />
            {/* facecam in the source */}
            <div className="absolute right-[4%] bottom-[6%] h-[30%] w-[22%] overflow-hidden rounded-lg ring-2 ring-[#FCAF45]/80">
              <Facecam />
              <span className="absolute -top-5 left-0 rounded bg-[#FCAF45] px-1 text-[9px] font-bold text-black">FACE</span>
            </div>
            {/* moving 9:16 crop window */}
            <motion.div
              className="absolute top-0 h-full w-[31.6%] rounded-md ring-2 ring-white shadow-[0_0_0_9999px_rgb(0_0_0/0.45)]"
              initial={{ left: "18%" }}
              animate={reduce ? undefined : { left: ["18%", "40%", "26%", "18%"] }}
              transition={{ duration: 7, repeat: Infinity, ease: "easeInOut" }}
            >
              <span className="absolute -top-px left-1/2 -translate-x-1/2 -translate-y-full rounded-t bg-white px-1.5 text-[9px] font-bold text-black">9:16</span>
            </motion.div>
          </div>
        </div>

        <motion.div
          aria-hidden
          className="hidden size-10 place-items-center rounded-full bg-brand-gradient md:grid"
          animate={reduce ? undefined : { scale: [1, 1.08, 1] }}
          transition={{ duration: 1.8, repeat: Infinity }}
        >
          <Sparkles className="size-5 text-white" />
        </motion.div>

        {/* Vertical output */}
        <div className="mx-auto w-[min(100%,220px)]">
          <div className="mb-2 text-center text-[11px] text-muted-foreground">Output · 9:16 facecam + gameplay</div>
          <div className="gradient-border relative aspect-[9/16] overflow-hidden rounded-[22px] bg-black p-1.5">
            <div className="relative flex h-full flex-col overflow-hidden rounded-[17px]">
              <div className="relative h-[38%] overflow-hidden">
                <Facecam />
              </div>
              <div className="h-px bg-white/30" />
              <div className="relative flex-1 overflow-hidden">
                <Gameplay zoom />
              </div>
            </div>
          </div>
        </div>
      </div>
      <p className="mt-3 text-center text-[11px] text-muted-foreground/70">Illustration of the framing workflow — not real footage or results.</p>
    </div>
  );
}

function Gameplay({ zoom = false }: { zoom?: boolean }) {
  return (
    <div className="absolute inset-0 overflow-hidden bg-[linear-gradient(180deg,#1b2a4a_0%,#23365e_45%,#2b1f3d_46%,#160f22_100%)]">
      <motion.div
        className="absolute inset-0"
        style={{ scale: zoom ? 1.6 : 1 }}
        animate={{ x: ["0%", "-4%", "0%"] }}
        transition={{ duration: 6, repeat: Infinity, ease: "easeInOut" }}
      >
        <div className="absolute bottom-[54%] left-[8%] h-[22%] w-[14%] rounded-t-md bg-[#3d5a8c]" />
        <div className="absolute bottom-[54%] left-[30%] h-[34%] w-[10%] rounded-t-md bg-[#4b6aa1]" />
        <div className="absolute bottom-[54%] left-[62%] h-[28%] w-[16%] rounded-t-md bg-[#36507e]" />
        <div className="absolute top-[56%] left-[40%] size-[9%] rounded-full bg-[#ff4d6d] shadow-[0_0_24px_#ff0033]" />
        <div className="absolute top-[48%] left-1/2 h-[2px] w-[22%] -translate-x-1/2 bg-gradient-to-r from-transparent via-[#FCAF45] to-transparent" />
      </motion.div>
      <div className="absolute top-[6%] left-[4%] h-[5%] w-[18%] rounded-sm bg-white/25" />
      <div className="absolute top-[6%] right-[4%] h-[5%] w-[10%] rounded-sm bg-[#FCAF45]/60" />
    </div>
  );
}

function Facecam() {
  return (
    <div className="absolute inset-0 bg-[radial-gradient(80%_80%_at_50%_30%,#4a3a5c,#1c1624)]">
      <div className="absolute top-[20%] left-1/2 aspect-square w-[34%] -translate-x-1/2 rounded-full bg-[#d9a58a]" />
      <div className="absolute top-[62%] left-1/2 h-[60%] w-[64%] -translate-x-1/2 rounded-t-[40%] bg-[#8134AF]" />
      <div className="absolute top-[14%] left-1/2 h-[12%] w-[38%] -translate-x-1/2 rounded-t-full bg-[#2a1d14]" />
    </div>
  );
}
