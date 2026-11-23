"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowRight, CalendarClock, Crop, FileVideo, ScanFace, Send, Sparkles, Tags, Wand2 } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { InstagramIcon, Logo, YouTubeIcon } from "@/components/common/brand";
import { cn } from "@/lib/utils";

const FEATURES = [
  {
    icon: Sparkles,
    title: "AI highlight detection",
    text: "Speech-to-text, audio energy, scene changes and visual motion are combined to find funny, emotional and high-energy moments — each with timestamps, reasons and a transcript preview. Scores are clearly labelled as AI estimates.",
    accent: "from-[#8134AF]/30",
  },
  {
    icon: Crop,
    title: "Smart framing, faces first",
    text: "Face tracking keeps speakers in frame. Switch to facecam + gameplay stacks, picture-in-picture or manual crops, and get a warning before anything important is cut off.",
    accent: "from-[#FCAF45]/25",
  },
  {
    icon: Tags,
    title: "Transcript, title & hashtags",
    text: "Every clip comes with its transcript plus a title, description and hashtags written by free local AI. Edit anything before it goes out. Clips are exported clean, with nothing burned in.",
    accent: "from-[#DD2A7B]/30",
  },
  {
    icon: CalendarClock,
    title: "Sequential YouTube uploads",
    text: "Upload the next clip or all remaining clips in timeline order through the official YouTube Data API. A failed upload stops the run until you retry, and local files are cleaned up once YouTube confirms the video.",
    accent: "from-[#FF0033]/25",
    id: "publishing",
  },
];

const STEPS = [
  { icon: FileVideo, title: "Add the video", text: "Paste a YouTube link (downloaded in the best quality available, up to 4K 60fps) or upload the video file." },
  { icon: Wand2, title: "Let AI find the moments", text: "Pick Fast, Balanced or Best and a clip length range — ClipFusion transcribes, analyses and cuts natural, self-contained clips automatically." },
  { icon: ScanFace, title: "Review & frame", text: "Check each clip in timeline order, keep faces visible in the 9:16 framing and edit its title, description and hashtags." },
  { icon: Send, title: "Upload one by one", text: "Upload the next clip or all remaining clips to YouTube Shorts; each clip's local files are deleted after a verified upload." },
];

const reveal = {
  initial: { opacity: 0, y: 18 },
  whileInView: { opacity: 1, y: 0 },
  viewport: { once: true, margin: "-60px" },
  transition: { duration: 0.45 },
};

export function Features() {
  return (
    <section id="features" className="scroll-mt-20 px-4 py-20 sm:px-6">
      <div className="mx-auto max-w-6xl">
        <motion.div {...reveal} className="mx-auto max-w-2xl text-center">
          <h2 className="font-heading text-3xl font-bold tracking-tight sm:text-4xl">Everything between “record” and “posted”</h2>
          <p className="mt-3 text-muted-foreground">One workspace for clipping, editing and publishing — no exporting between five different tools.</p>
        </motion.div>
        <div className="mt-12 grid gap-5 md:grid-cols-2">
          {FEATURES.map((f, i) => (
            <motion.article
              key={f.title}
              id={f.id}
              {...reveal}
              transition={{ duration: 0.45, delay: i * 0.06 }}
              whileHover={{ y: -4 }}
              className={cn("group relative scroll-mt-24 overflow-hidden rounded-3xl border border-white/8 bg-card/60 p-7 bg-gradient-to-br to-transparent to-60%", f.accent)}
            >
              <span className="gradient-border grid size-11 place-items-center rounded-2xl bg-black/30">
                <f.icon className="size-5" />
              </span>
              <h3 className="mt-5 font-heading text-xl font-semibold">{f.title}</h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{f.text}</p>
              {f.id === "publishing" && (
                <div className="mt-5 flex items-center gap-3 text-xs text-muted-foreground">
                  <span className="flex items-center gap-1.5 rounded-full bg-white/5 px-2.5 py-1"><YouTubeIcon /> YouTube Shorts</span>
                  <span className="flex items-center gap-1.5 rounded-full bg-white/5 px-2.5 py-1"><InstagramIcon /> Instagram Reels</span>
                </div>
              )}
            </motion.article>
          ))}
        </div>
      </div>
    </section>
  );
}

export function HowItWorks() {
  return (
    <section id="how-it-works" className="scroll-mt-20 px-4 py-20 sm:px-6">
      <div className="mx-auto max-w-6xl">
        <motion.div {...reveal} className="text-center">
          <h2 className="font-heading text-3xl font-bold tracking-tight sm:text-4xl">How it works</h2>
          <p className="mt-3 text-muted-foreground">Four steps from a long recording to a publishing schedule.</p>
        </motion.div>
        <ol className="relative mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          {STEPS.map((s, i) => (
            <motion.li key={s.title} {...reveal} transition={{ duration: 0.45, delay: i * 0.08 }} className="relative rounded-3xl border border-white/8 bg-card/50 p-6">
              <span className="font-heading text-xs font-bold text-gradient">STEP {i + 1}</span>
              <s.icon className="mt-4 size-6 text-foreground/90" />
              <h3 className="mt-3 font-heading text-lg font-semibold">{s.title}</h3>
              <p className="mt-1.5 text-sm text-muted-foreground">{s.text}</p>
            </motion.li>
          ))}
        </ol>
      </div>
    </section>
  );
}

export function FinalCta() {
  return (
    <section className="px-4 pt-10 pb-24 sm:px-6">
      <motion.div
        {...reveal}
        className="gradient-border relative mx-auto max-w-5xl overflow-hidden rounded-[32px] bg-[radial-gradient(70%_120%_at_0%_0%,rgb(129_52_175/0.35),transparent),radial-gradient(70%_120%_at_100%_100%,rgb(255_0_51/0.3),transparent)] px-6 py-14 text-center sm:px-12"
      >
        <h2 className="font-heading text-3xl font-bold tracking-tight sm:text-4xl">Your next Shorts are already in your last stream.</h2>
        <p className="mx-auto mt-3 max-w-xl text-muted-foreground">Open the studio, add a video, and review your first AI-selected clips.</p>
        <Link href="/app" className={cn(buttonVariants({ size: "lg" }), "glow-red mt-8 h-11 rounded-xl px-6 text-[15px]")}>
          Open the studio <ArrowRight />
        </Link>
      </motion.div>
    </section>
  );
}

export function LandingFooter() {
  return (
    <footer className="border-t border-white/6 px-4 py-8 sm:px-6">
      <div className="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 text-xs text-muted-foreground sm:flex-row">
        <Logo />
        <p>Only process and publish videos you have the creator&apos;s permission, a licence or other rights to reuse. YouTube and Instagram are trademarks of their respective companies.</p>
      </div>
    </footer>
  );
}
