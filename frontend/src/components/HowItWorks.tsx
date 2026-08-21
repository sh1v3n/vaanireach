"use client";

import { motion } from "framer-motion";

const STEPS = [
  { title: "Upload", body: "Upload a government notice (.txt/.pdf, including scanned documents via OCR) or paste the text directly." },
  { title: "Extract & Verify", body: "Facts are extracted, narration is drafted per scene, and every claim is checked against the source before anything is rendered." },
  { title: "Review & Approve", body: "See the script, detected facts, and verification results per language before you approve a video for publication." },
];

export function HowItWorks() {
  return (
    <section className="mx-auto max-w-4xl px-6 py-16">
      <h2 className="mb-10 text-center font-serifDisplay text-2xl text-navy-dark">How it works</h2>
      <div className="grid gap-8 md:grid-cols-3">
        {STEPS.map((step, i) => (
          <motion.div
            key={step.title}
            initial={{ opacity: 0, y: 24 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: i * 0.15 }}
            className="rounded-lg border border-gold/30 bg-white p-6 shadow-sm"
          >
            <div className="mb-3 font-serifDisplay text-3xl text-gold">{i + 1}</div>
            <h3 className="mb-2 font-semibold text-navy-dark">{step.title}</h3>
            <p className="text-sm text-navy-light">{step.body}</p>
          </motion.div>
        ))}
      </div>
    </section>
  );
}
