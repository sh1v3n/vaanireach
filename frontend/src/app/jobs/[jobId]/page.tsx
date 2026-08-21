"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";

import { FactLedgerFlow } from "@/components/FactLedgerFlow";
import { ReviewCard } from "@/components/ReviewCard";
import { WorkflowSteps } from "@/components/WorkflowSteps";
import { getJob } from "@/lib/api-client";
import type { JobView, LanguageCode } from "@/types";

function hasRegeneratingLanguage(j: JobView): boolean {
  return Object.values(j.languages).some((lang) => lang.regenerating);
}

export default function JobPage() {
  const params = useParams<{ jobId: string }>();
  const jobId = params.jobId;
  const [job, setJob] = useState<JobView | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const jobRef = useRef<JobView | null>(null);
  jobRef.current = job;

  const refresh = useCallback(async () => {
    try {
      const data = await getJob(jobId);
      setJob(data);
      setPollError(null);
    } catch (err) {
      setPollError(err instanceof Error ? err.message : "Failed to load job.");
    }
  }, [jobId]);

  useEffect(() => {
    refresh();
    const interval = setInterval(() => {
      const current = jobRef.current;
      if (
        current &&
        (current.status === "pending_review" || current.status === "failed") &&
        !hasRegeneratingLanguage(current)
      ) {
        clearInterval(interval);
        return;
      }
      refresh();
    }, 3000);
    return () => clearInterval(interval);
  }, [refresh]);

  if (pollError) {
    return <main className="min-h-screen bg-navy p-10 text-white">Error: {pollError}</main>;
  }
  if (!job) {
    return <main className="min-h-screen bg-navy p-10 text-white">Loading…</main>;
  }

  return (
    <main className="min-h-screen bg-navy">
      <header className="bg-navy px-6 py-10 text-white">
        <h1 className="font-serifDisplay text-3xl text-gold">VaaniReach</h1>
        <p className="mt-1 text-sm text-white/70">Job {job.job_id}</p>
        <p className="mt-1 text-xs capitalize text-white/50">
          {job.voice.speaker} · {job.voice.gender} · {job.voice.style} · pace {job.voice.pace.toFixed(2)}×
          {job.voice.pitch !== null && ` · pitch ${job.voice.pitch.toFixed(2)}`}
        </p>
      </header>

      <div className="bg-white px-6 py-10">
        {(job.status === "pending" || job.status === "running") && (
          <div className="mx-auto mb-10 max-w-2xl">
            <p className="mb-6 text-center text-sm text-navy-light">
              Generating your video — real fact extraction, translation, TTS, and avatar lip-sync.
              This takes a few minutes; you&apos;ll see each step below as it happens.
            </p>
            <WorkflowSteps stage={job.stage} />
          </div>
        )}

        {job.status === "failed" && (
          <p className="mx-auto max-w-2xl rounded border border-red-300 bg-red-50 p-4 text-red-700">
            Generation failed: {job.error}
          </p>
        )}

        {(job.status === "pending" || job.status === "running" || job.facts.length > 0) && (
          <FactLedgerFlow facts={job.facts} stage={job.stage} />
        )}

        <div className="mx-auto grid max-w-4xl gap-6">
          {(Object.entries(job.languages) as [LanguageCode, JobView["languages"][LanguageCode]][]).map(
            ([language, view]) => (
              <ReviewCard key={language} jobId={job.job_id} language={language} view={view} onChanged={refresh} />
            ),
          )}
        </div>
      </div>
    </main>
  );
}
