"use client";

import type { ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";

import type { PipelineFact, PipelineStage } from "@/types";

function formatFactType(factType: string): string {
  return factType.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function DocumentIcon() {
  return (
    <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M19.5 14.25v-2.625a3.375 3.375 0 0 0-3.375-3.375h-1.5A1.125 1.125 0 0 1 13.5 7.125v-1.5a3.375 3.375 0 0 0-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 0 0-9-9Z"
      />
    </svg>
  );
}

function SearchIcon({ spinning }: { spinning?: boolean }) {
  return (
    <motion.svg
      animate={spinning ? { rotate: [0, 15, -15, 0] } : { rotate: 0 }}
      transition={{ duration: 1.4, repeat: spinning ? Infinity : 0, ease: "easeInOut" }}
      className="h-6 w-6"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={1.5}
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" />
    </motion.svg>
  );
}

function LedgerIcon() {
  return (
    <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M20.25 7.5 19.625 18.132a2.25 2.25 0 0 1-2.247 2.118H6.622a2.25 2.25 0 0 1-2.247-2.118L3.75 7.5M10 11.25h4M3.375 7.5h17.25c.621 0 1.125-.504 1.125-1.125v-1.5c0-.621-.504-1.125-1.125-1.125H3.375c-.621 0-1.125.504-1.125 1.125v1.5c0 .621.504 1.125 1.125 1.125Z"
      />
    </svg>
  );
}

function FlowNode({ label, children, done, active }: { label: string; children: ReactNode; done?: boolean; active?: boolean }) {
  return (
    <div
      className={`flex shrink-0 flex-row items-center gap-2 rounded-lg border-2 px-4 py-3 text-left transition-colors md:w-36 md:flex-col md:text-center ${
        active ? "border-gold bg-gold/10" : done ? "border-gold/50 bg-gold/5" : "border-navy-light/30"
      }`}
    >
      <span className={active || done ? "text-gold" : "text-navy-light/50"}>{children}</span>
      <span className={`text-xs font-medium ${active ? "text-navy-dark" : "text-navy-light"}`}>{label}</span>
    </div>
  );
}

function Connector({ active }: { active: boolean }) {
  return (
    <div className="flex shrink-0 items-center justify-center py-1 md:w-8 md:py-0">
      <motion.svg
        animate={active ? { opacity: [0.4, 1, 0.4] } : { opacity: 0.3 }}
        transition={{ duration: 1.4, repeat: active ? Infinity : 0, ease: "easeInOut" }}
        className={`h-5 w-5 rotate-90 md:rotate-0 ${active ? "text-gold" : "text-navy-light/40"}`}
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
        strokeWidth={2}
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5 21 12m0 0-7.5 7.5M21 12H3" />
      </motion.svg>
    </div>
  );
}

/** Visualizes fact extraction as a live pipeline flow — Document →
 * Extracting → Source Fact Ledger — instead of a static checklist. Each
 * detected fact flies in as a chip and lands in the Ledger box, so "facts
 * flow into a structured, provenance-tracked ledger" (the product's core
 * pitch) is something the user watches happen rather than just reads
 * about. Hover/focus a chip to see the exact source-document quote it
 * was extracted from. */
export function FactLedgerFlow({ facts, stage }: { facts: PipelineFact[]; stage: PipelineStage | null }) {
  const isExtracting = stage === "extracting_facts";
  const factsFound = facts.length > 0;
  const ledgerActive = factsFound || isExtracting;

  return (
    <div className="mx-auto mb-8 max-w-4xl rounded-lg border border-gold/30 bg-gold/5 p-4 md:p-6">
      <h2 className="mb-4 text-sm font-semibold text-navy-dark">Source Fact Ledger</h2>
      <div className="flex flex-col items-stretch gap-1 md:flex-row md:items-center md:gap-0">
        <FlowNode label="Your document" done>
          <DocumentIcon />
        </FlowNode>

        <Connector active={isExtracting} />

        <FlowNode label="Extracting facts" done={factsFound && !isExtracting} active={isExtracting}>
          <SearchIcon spinning={isExtracting} />
        </FlowNode>

        <Connector active={ledgerActive} />

        <div
          className={`min-h-[4.5rem] flex-1 rounded-lg border-2 p-3 transition-colors ${
            ledgerActive ? "border-gold bg-white" : "border-navy-light/30 bg-navy/[0.02]"
          }`}
        >
          <div className="mb-2 flex items-center gap-1.5 text-[0.65rem] font-semibold uppercase tracking-wide text-navy-light/70">
            <LedgerIcon />
            Ledger
          </div>
          {factsFound ? (
            <div className="flex flex-wrap gap-2">
              <AnimatePresence>
                {facts.map((fact, i) => (
                  <motion.div
                    key={fact.id}
                    initial={{ opacity: 0, scale: 0.8, x: -12 }}
                    animate={{ opacity: 1, scale: 1, x: 0 }}
                    transition={{ duration: 0.35, delay: i * 0.05 }}
                    tabIndex={0}
                    title={`${fact.value} — "${fact.source_span.text_span}"`}
                    className="cursor-default rounded-full border border-gold/40 bg-gold/5 px-3 py-1 text-xs text-navy-dark shadow-sm"
                  >
                    <span className="font-medium text-gold">{formatFactType(fact.fact_type)}</span>
                    <span className="ml-1.5 text-navy-light">{fact.value}</span>
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
          ) : (
            <p className="text-xs text-navy-light/60">Facts will appear here as they&apos;re found…</p>
          )}
        </div>
      </div>
    </div>
  );
}
