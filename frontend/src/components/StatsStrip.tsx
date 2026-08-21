"use client";

import { motion } from "framer-motion";

// Real system capabilities, not fabricated usage/social-proof numbers —
// this is a demo tool, not a live product with real users to cite.
const STATS = [
  { value: "9", label: "Indian languages" },
  { value: "100%", label: "Fact-verified before publish" },
  { value: "6", label: "Live pipeline stages you can watch" },
  { value: "0", label: "Videos published without human approval" },
];

export function StatsStrip() {
  return (
    <section className="border-y border-gold/20 bg-navy-dark px-6 py-10 text-white">
      <div className="mx-auto grid max-w-5xl grid-cols-2 gap-8 md:grid-cols-4">
        {STATS.map((stat, i) => (
          <motion.div
            key={stat.label}
            initial={{ opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.4, delay: i * 0.08 }}
            className="text-center"
          >
            <div className="font-serifDisplay text-3xl text-gold md:text-4xl">{stat.value}</div>
            <div className="mt-1 text-xs text-white/60 md:text-sm">{stat.label}</div>
          </motion.div>
        ))}
      </div>
    </section>
  );
}
