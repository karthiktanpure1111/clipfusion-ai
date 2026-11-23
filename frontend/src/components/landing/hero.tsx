"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import { ArrowRight, ShieldCheck } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { Logo } from "@/components/common/brand";
import { cn } from "@/lib/utils";
import { FramingVisual } from "./framing-visual";

export function LandingHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-white/6 bg-background/70 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Link href="/" className="rounded-xl focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none" aria-label="ClipFusion AI home">
          <Logo />
        </Link>
        <nav className="hidden items-center gap-7 text-sm text-muted-foreground md:flex" aria-label="Landing">
          <a href="#features" className="hover:text-foreground">Features</a>
          <a href="#how-it-works" className="hover:text-foreground">How it works</a>
          <a href="#publishing" className="hover:text-foreground">Publishing</a>
        </nav>
        <div className="flex items-center gap-2">
          <Link href="/app" className={cn(buttonVariants({ size: "lg" }), "glow-red rounded-xl px-4")}>
            Open the studio
          </Link>
        </div>
      </div>
    </header>
  );
}

export function Hero() {
  return (
    <section className="relative overflow-hidden px-4 pt-16 pb-20 sm:px-6 sm:pt-24">
      <div aria-hidden className="absolute inset-x-0 top-0 -z-10 h-[600px] bg-[radial-gradient(50%_60%_at_50%_0%,rgb(129_52_175/0.22),transparent)]" />
      <div className="mx-auto max-w-4xl text-center">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="gradient-border mx-auto mb-6 inline-flex items-center gap-2 rounded-full bg-white/3 px-3.5 py-1.5 text-xs text-muted-foreground"
        >
          <span className="size-1.5 rounded-full bg-ig-gradient" /> For streamers, podcasters &amp; creators
        </motion.div>
        <motion.h1
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.05 }}
          className="font-heading text-4xl leading-[1.05] font-extrabold tracking-tight text-balance sm:text-6xl"
        >
          Turn Long Videos Into <span className="text-gradient">Viral Shorts</span> Automatically.
        </motion.h1>
        <motion.p
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.12 }}
          className="mx-auto mt-6 max-w-2xl text-base text-pretty text-muted-foreground sm:text-lg"
        >
          AI-powered clipping, smart framing, ready-made titles and hashtags, and one-by-one YouTube uploads — all in one place.
        </motion.p>
        <motion.div
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5, delay: 0.2 }}
          className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row"
        >
          <Link href="/app" className={cn(buttonVariants({ size: "lg" }), "glow-red h-11 rounded-xl px-6 text-[15px]")}>
            Open the studio <ArrowRight />
          </Link>
          <a href="#how-it-works" className={cn(buttonVariants({ variant: "outline", size: "lg" }), "h-11 rounded-xl px-6 text-[15px]")}>
            See how it works
          </a>
        </motion.div>
        <p className="mt-5 flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
          <ShieldCheck className="size-3.5" /> Official YouTube &amp; Instagram APIs only — we never ask for your passwords.
        </p>
      </div>
      <motion.div
        initial={{ opacity: 0, y: 30 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.7, delay: 0.25 }}
        className="mt-16"
      >
        <FramingVisual />
      </motion.div>
    </section>
  );
}
