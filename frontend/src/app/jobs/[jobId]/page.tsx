"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";

import { ReviewCard } from "@/components/ReviewCard";
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
      </header>

      <div className="bg-white px-6 py-10">
        {(job.status === "pending" || job.status === "running") && (
          <p className="mx-auto max-w-2xl text-center text-navy-dark">
            Generating your videos — real fact extraction, translation, TTS, and avatar lip-sync per
            language. This takes a few minutes.
          </p>
        )}

        {job.status === "failed" && (
          <p className="mx-auto max-w-2xl rounded border border-red-300 bg-red-50 p-4 text-red-700">
            Generation failed: {job.error}
          </p>
        )}

        {job.facts.length > 0 && (
          <div className="mx-auto mb-8 max-w-4xl rounded-lg border border-gold/30 bg-gold/5 p-4">
            <h2 className="mb-2 text-sm font-semibold text-navy-dark">Detected facts</h2>
            <ul className="space-y-1 text-sm text-navy-light">
              {job.facts.map((fact) => (
                <li key={fact.id}>
                  <span className="font-medium capitalize">{fact.fact_type}</span>: {fact.value}
                  <span className="ml-2 text-xs italic">— &quot;{fact.source_span.text_span}&quot;</span>
                </li>
              ))}
            </ul>
          </div>
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
