"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";

import { LanguagePicker } from "@/components/LanguagePicker";
import { HowItWorks } from "@/components/HowItWorks";
import { VoicePicker } from "@/components/VoicePicker";
import { createJob } from "@/lib/api-client";
import type { LanguageCode, NarrationStyle } from "@/types";

export default function HomePage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [languages, setLanguages] = useState<LanguageCode[]>(["en"]);
  const [speaker, setSpeaker] = useState("shubh");
  const [style, setStyle] = useState<NarrationStyle>("news");
  const [pace, setPace] = useState(1.1); // matches the backend's news-style default (DEFAULT_PACE_FOR_STYLE)
  const [pitch, setPitch] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleStyleChange(next: NarrationStyle) {
    setStyle(next);
    setPace(next === "news" ? 1.1 : 0.95); // matches DEFAULT_PACE_FOR_STYLE — still user-overridable via the slider
  }

  async function handleSubmit() {
    setError(null);
    if (!file && !text.trim()) {
      setError("Upload a file or paste some text first.");
      return;
    }
    if (languages.length === 0) {
      setError("Pick at least one language.");
      return;
    }
    setSubmitting(true);
    try {
      const { job_id } = await createJob({
        languages, file: file ?? undefined, text: text.trim() || undefined,
        speaker, style, pace, pitch: pitch ?? undefined,
      });
      router.push(`/jobs/${job_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
      setSubmitting(false);
    }
  }

  return (
    <main>
      <section className="relative overflow-hidden bg-navy px-6 py-20 text-white">
        <div className="mx-auto max-w-4xl">
          <motion.h1
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6 }}
            className="font-serifDisplay text-4xl font-bold text-gold md:text-5xl"
          >
            VaaniReach
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.1 }}
            className="mt-3 max-w-xl text-white/80"
          >
            Turn a government notice into a multilingual, fact-verified narrated video — ready for a
            human to review and approve before it&apos;s published.
          </motion.p>

          <motion.div
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.2 }}
            className="mt-10 rounded-xl bg-white p-6 text-navy-dark shadow-xl md:p-8"
          >
            <label className="mb-1 block text-sm font-medium">Government notice</label>
            <input
              type="file"
              accept=".txt,.pdf"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="mb-4 block w-full text-sm"
            />
            <label className="mb-1 block text-sm font-medium">...or paste the notice text directly</label>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={5}
              placeholder="Paste raw notice text here if you don't have a file handy."
              className="mb-4 w-full rounded border border-navy-light/30 p-3 text-sm"
            />
            <label className="mb-2 block text-sm font-medium">Languages to generate</label>
            <LanguagePicker selected={languages} onChange={setLanguages} />

            <div className="mt-6 border-t border-navy-light/20 pt-6">
              <VoicePicker
                speaker={speaker} onSpeakerChange={setSpeaker}
                style={style} onStyleChange={handleStyleChange}
                pace={pace} onPaceChange={setPace}
                pitch={pitch} onPitchChange={setPitch}
              />
            </div>

            {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

            <button
              type="button"
              onClick={handleSubmit}
              disabled={submitting}
              className="mt-6 rounded-full bg-gold px-6 py-2.5 font-medium text-navy-dark transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {submitting ? "Starting…" : "Generate video(s)"}
            </button>
          </motion.div>
        </div>
      </section>

      <div className="bg-white">
        <HowItWorks />
      </div>
    </main>
  );
}
