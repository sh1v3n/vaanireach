"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";

import { DropZone } from "@/components/DropZone";
import { Hero } from "@/components/Hero";
import { HowItWorks } from "@/components/HowItWorks";
import { LanguagePicker } from "@/components/LanguagePicker";
import { StatsStrip } from "@/components/StatsStrip";
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

  // Clear a stale validation error as soon as the user starts fixing the
  // input that caused it, instead of leaving it on screen until the next
  // submit click.
  function handleFileChange(next: File | null) {
    setFile(next);
    setError(null);
  }

  function handleTextChange(next: string) {
    setText(next);
    setError(null);
  }

  function handleLanguagesChange(next: LanguageCode[]) {
    setLanguages(next);
    setError(null);
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
      <Hero />
      <StatsStrip />

      <div className="bg-white">
        <HowItWorks />
      </div>

      <section id="upload" className="bg-navy px-6 py-20">
        <div className="mx-auto max-w-2xl">
          <motion.div
            initial={{ opacity: 0, y: 24 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.2 }}
            transition={{ duration: 0.5 }}
            className="mb-8 text-center text-white"
          >
            <h2 className="font-serifDisplay text-3xl text-gold">Generate your video</h2>
            <p className="mt-2 text-sm text-white/70">Upload a notice, pick languages and a voice, and let the pipeline do the rest.</p>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 24 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.1 }}
            transition={{ duration: 0.5, delay: 0.1 }}
            className="rounded-xl bg-white p-6 text-navy-dark shadow-2xl md:p-8"
          >
            <label className="mb-2 block text-sm font-medium">Government notice</label>
            <DropZone file={file} onFileChange={handleFileChange} />

            <label className="mb-1 mt-5 block text-sm font-medium">...or paste the notice text directly</label>
            <textarea
              value={text}
              onChange={(e) => handleTextChange(e.target.value)}
              rows={5}
              placeholder="Paste raw notice text here if you don't have a file handy."
              className="mb-4 w-full rounded border border-navy-light/30 p-3 text-sm"
            />
            <label className="mb-2 block text-sm font-medium">Languages to generate</label>
            <LanguagePicker selected={languages} onChange={handleLanguagesChange} />

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
              className="mt-6 w-full cursor-pointer rounded-full bg-gold px-6 py-3 font-medium text-navy-dark transition-transform hover:scale-[1.02] active:scale-100 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:scale-100"
            >
              {submitting ? "Starting…" : "Generate video(s)"}
            </button>
          </motion.div>
        </div>
      </section>

      <footer className="bg-navy-dark px-6 py-6 text-center text-xs text-white/40">
        VaaniReach — multilingual outreach video generator
      </footer>
    </main>
  );
}
