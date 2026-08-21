"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";

import type { PipelineScene } from "@/types";

function formatRole(role: string): string {
  return role.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function CopyIcon() {
  return (
    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M15.75 17.25v3.375c0 .621-.504 1.125-1.125 1.125h-9.75a1.125 1.125 0 0 1-1.125-1.125V7.875c0-.621.504-1.125 1.125-1.125H6.75a9.06 9.06 0 0 1 1.5.124m7.5 10.376h3.375c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9H8.625c-.621 0-1.125.504-1.125 1.125v1.5"
      />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
    </svg>
  );
}

/** A quick-scan bullet summary of the generated video's narration, built
 * directly from the already-verified, already-in-target-language script
 * scenes — no new generation call, no invented content, just the same
 * sentences the reviewer already checked above, condensed into a
 * skimmable list with a one-click copy for pasting into a memo/email. */
export function DocumentSummary({ scenes }: { scenes: PipelineScene[] }) {
  const [copied, setCopied] = useState(false);

  if (scenes.length === 0) return null;

  const ordered = [...scenes].sort((a, b) => a.order_index - b.order_index);
  const summaryText = ordered.map((s) => `• ${formatRole(s.narrative_role)}: ${s.narration_segment_text}`).join("\n");

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(summaryText);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be denied by the browser (e.g. insecure
      // context) — fail quietly rather than showing a false "Copied!".
    }
  }

  return (
    <div className="mb-4 rounded-lg border border-gold/30 bg-gold/5 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h4 className="text-sm font-semibold text-navy-dark">Document Summary</h4>
        <button
          type="button"
          onClick={handleCopy}
          className="flex cursor-pointer items-center gap-1.5 rounded-full border border-gold/40 bg-white px-3 py-1 text-xs font-medium text-navy-dark transition-colors hover:bg-gold/10"
        >
          <AnimatePresence mode="wait" initial={false}>
            {copied ? (
              <motion.span
                key="copied"
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.8 }}
                transition={{ duration: 0.15 }}
                className="flex items-center gap-1.5 text-green-700"
              >
                <CheckIcon /> Copied!
              </motion.span>
            ) : (
              <motion.span
                key="copy"
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.8 }}
                transition={{ duration: 0.15 }}
                className="flex items-center gap-1.5"
              >
                <CopyIcon /> Copy text
              </motion.span>
            )}
          </AnimatePresence>
        </button>
      </div>

      <ul className="space-y-2">
        {ordered.map((scene, i) => (
          <motion.li
            key={scene.id}
            initial={{ opacity: 0, x: -12 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true, amount: 0.4 }}
            transition={{ duration: 0.35, delay: i * 0.06 }}
            className="flex gap-2 text-sm text-navy-dark"
          >
            <span className="mt-0.5 shrink-0 text-gold">•</span>
            <span>
              <span className="font-medium text-gold">{formatRole(scene.narrative_role)}:</span>{" "}
              {scene.narration_segment_text}
            </span>
          </motion.li>
        ))}
      </ul>
    </div>
  );
}
