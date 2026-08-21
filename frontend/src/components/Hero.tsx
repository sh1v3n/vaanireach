"use client";

import { motion } from "framer-motion";

/**
 * Full-bleed marketing hero — headline, tagline, and a single primary CTA
 * that smooth-scrolls to the upload section (#upload). Deliberately no
 * form here: per the hero-centric landing pattern, the hero's only job
 * is to earn the scroll, not to also be the form.
 */
export function Hero() {
  function scrollToUpload() {
    document.getElementById("upload")?.scrollIntoView({ behavior: "smooth" });
  }

  return (
    <section className="relative flex min-h-[92vh] items-center overflow-hidden bg-navy px-6 text-white">
      {/* Subtle animated background glow — navy/gold only, no purple/pink AI-gradient cliché */}
      <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
        <motion.div
          animate={{ x: [0, 30, 0], y: [0, -20, 0] }}
          transition={{ duration: 18, repeat: Infinity, ease: "easeInOut" }}
          className="absolute -left-32 -top-32 h-[36rem] w-[36rem] rounded-full bg-gold/10 blur-3xl"
        />
        <motion.div
          animate={{ x: [0, -20, 0], y: [0, 25, 0] }}
          transition={{ duration: 22, repeat: Infinity, ease: "easeInOut" }}
          className="absolute -right-24 bottom-0 h-[30rem] w-[30rem] rounded-full bg-navy-light/60 blur-3xl"
        />
        <svg className="absolute inset-0 h-full w-full opacity-[0.06]" preserveAspectRatio="none">
          <pattern id="grid" width="48" height="48" patternUnits="userSpaceOnUse">
            <path d="M 48 0 L 0 0 0 48" fill="none" stroke="white" strokeWidth="1" />
          </pattern>
          <rect width="100%" height="100%" fill="url(#grid)" />
        </svg>
      </div>

      <div className="relative mx-auto max-w-4xl py-24">
        <motion.span
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.5 }}
          className="mb-4 inline-block rounded-full border border-gold/40 px-3 py-1 text-xs uppercase tracking-widest text-gold"
        >
          Multilingual Government Outreach
        </motion.span>

        <motion.h1
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.05 }}
          className="font-serifDisplay text-5xl font-bold leading-tight text-gold md:text-7xl"
        >
          VaaniReach
        </motion.h1>

        <motion.p
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.15 }}
          className="mt-5 max-w-2xl text-lg text-white/80 md:text-xl"
        >
          Turn a government notice into a multilingual, fact-verified narrated video —
          ready for a human to review and approve before it&apos;s published.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.25 }}
          className="mt-10 flex flex-wrap items-center gap-4"
        >
          <button
            type="button"
            onClick={scrollToUpload}
            className="cursor-pointer rounded-full bg-gold px-8 py-3.5 text-base font-medium text-navy-dark shadow-lg shadow-gold/20 transition-transform hover:scale-105 active:scale-100"
          >
            Get started — upload a notice
          </button>
          <a href="#how-it-works" className="cursor-pointer text-sm text-white/70 underline underline-offset-4 hover:text-white">
            See how it works
          </a>
        </motion.div>
      </div>

      <motion.button
        type="button"
        onClick={scrollToUpload}
        aria-label="Scroll to upload section"
        animate={{ y: [0, 8, 0] }}
        transition={{ duration: 1.8, repeat: Infinity, ease: "easeInOut" }}
        className="absolute bottom-8 left-1/2 -translate-x-1/2 cursor-pointer text-gold/70 hover:text-gold"
      >
        <svg className="h-7 w-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 8.25 12 15.75 4.5 8.25" />
        </svg>
      </motion.button>
    </section>
  );
}
