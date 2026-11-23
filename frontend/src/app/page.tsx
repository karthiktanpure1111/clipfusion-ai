import { Hero, LandingHeader } from "@/components/landing/hero";
import { Features, FinalCta, HowItWorks, LandingFooter } from "@/components/landing/sections";

export default function LandingPage() {
  return (
    <div className="flex min-h-dvh flex-col">
      <LandingHeader />
      <main className="flex-1">
        <Hero />
        <Features />
        <HowItWorks />
        <FinalCta />
      </main>
      <LandingFooter />
    </div>
  );
}
