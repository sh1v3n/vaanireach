"use client";

import { motion } from "framer-motion";

import type { PipelineStage } from "@/types";

const STAGE_ORDER: PipelineStage[] = [
  "extracting_facts",
  "facts_extracted",
  "planning_narrative",
  "drafting_narration",
  "rendering_images",
  "generating_video",
];

const STAGE_LABELS: Record<PipelineStage, string> = {
  extracting_facts: "Extracting facts from your document",
  facts_extracted: "Facts found",
  planning_narrative: "Planning the video's narrative",
  drafting_narration: "Drafting narration",
  rendering_images: "Generating background visuals",
  generating_video: "Generating video(s)",
};

/** Live workflow visualization for the "pending"/"running" job states —
 * so the user can see what's actually happening instead of staring at
 * a static "generating…" message for several minutes. Driven by
 * job.stage (see rendering/multilingual_video.py's run_full_pipeline
 * on_stage docstring for the exact stage names/order this mirrors). */
export function WorkflowSteps({ stage }: { stage: PipelineStage | null }) {
  const currentIndex = stage ? STAGE_ORDER.indexOf(stage) : -1;

  return (
    <div className="mx-auto max-w-md space-y-3">
      {STAGE_ORDER.map((step, i) => {
        const isDone = currentIndex > i;
        const isActive = currentIndex === i;
        return (
          <motion.div
            key={step}
            initial={{ opacity: 0, x: -8 }}
            animate={{ opacity: isDone || isActive ? 1 : 0.4, x: 0 }}
            transition={{ duration: 0.3 }}
            className="flex items-center gap-3"
          >
            <span
              className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-medium ${
                isDone
                  ? "bg-gold text-navy-dark"
                  : isActive
                    ? "border-2 border-gold text-gold"
                    : "border border-navy-light/40 text-navy-light/60"
              }`}
            >
              {isDone ? "✓" : i + 1}
            </span>
            <span className={`text-sm ${isActive ? "font-medium text-navy-dark" : "text-navy-light"}`}>
              {STAGE_LABELS[step]}
              {isActive && (
                <motion.span
                  animate={{ opacity: [0.3, 1, 0.3] }}
                  transition={{ duration: 1.2, repeat: Infinity }}
                  className="ml-1"
                >
                  …
                </motion.span>
              )}
            </span>
          </motion.div>
        );
      })}
    </div>
  );
}
