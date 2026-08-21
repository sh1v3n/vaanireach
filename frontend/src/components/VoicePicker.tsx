"use client";

import { useEffect, useState } from "react";

import { listVoices } from "@/lib/api-client";
import type { NarrationStyle, Voice } from "@/types";

const STYLE_LABELS: Record<NarrationStyle, { label: string; hint: string }> = {
  news: { label: "News", hint: "Crisp, formal — short declarative sentences" },
  storytelling: { label: "Storytelling", hint: "Warmer, narrative — direct address to the viewer" },
};

export function VoicePicker({
  speaker,
  onSpeakerChange,
  style,
  onStyleChange,
  pace,
  onPaceChange,
  pitch,
  onPitchChange,
}: {
  speaker: string;
  onSpeakerChange: (speaker: string) => void;
  style: NarrationStyle;
  onStyleChange: (style: NarrationStyle) => void;
  pace: number;
  onPaceChange: (pace: number) => void;
  pitch: number | null;
  onPitchChange: (pitch: number | null) => void;
}) {
  const [voices, setVoices] = useState<Voice[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    listVoices()
      .then(setVoices)
      .catch((err) => setLoadError(err instanceof Error ? err.message : "Failed to load voices."));
  }, []);

  const selectedVoice = voices.find((v) => v.speaker === speaker);
  const pitchAvailable = selectedVoice?.supports_pitch ?? false;

  function selectSpeaker(next: Voice) {
    onSpeakerChange(next.speaker);
    if (!next.supports_pitch) onPitchChange(null); // clear a now-unsupported pitch value
  }

  if (loadError) {
    return <p className="text-sm text-red-600">Couldn&apos;t load voices: {loadError}</p>;
  }

  const maleVoices = voices.filter((v) => v.gender === "male");
  const femaleVoices = voices.filter((v) => v.gender === "female");

  return (
    <div className="space-y-4">
      <div>
        <label className="mb-2 block text-sm font-medium">Voice</label>
        {voices.length === 0 ? (
          <p className="text-sm text-navy-light">Loading voices…</p>
        ) : (
          <div className="space-y-2">
            {[
              { label: "Male", list: maleVoices },
              { label: "Female", list: femaleVoices },
            ].map(({ label, list }) => (
              <div key={label} className="flex flex-wrap items-center gap-2">
                <span className="w-14 shrink-0 text-xs uppercase tracking-wide text-navy-light">{label}</span>
                {list.map((v) => (
                  <button
                    key={v.speaker}
                    type="button"
                    onClick={() => selectSpeaker(v)}
                    className={`rounded-full border px-3 py-1 text-sm capitalize transition-colors ${
                      speaker === v.speaker
                        ? "border-gold bg-gold text-navy-dark font-medium"
                        : "border-navy-light bg-transparent text-navy hover:border-gold"
                    }`}
                  >
                    {v.speaker}
                  </button>
                ))}
              </div>
            ))}
          </div>
        )}
      </div>

      <div>
        <label className="mb-2 block text-sm font-medium">Narration style</label>
        <div className="flex gap-2">
          {(Object.keys(STYLE_LABELS) as NarrationStyle[]).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => onStyleChange(s)}
              title={STYLE_LABELS[s].hint}
              className={`rounded-full border px-4 py-1.5 text-sm transition-colors ${
                style === s
                  ? "border-gold bg-gold text-navy-dark font-medium"
                  : "border-navy-light bg-transparent text-navy hover:border-gold"
              }`}
            >
              {STYLE_LABELS[s].label}
            </button>
          ))}
        </div>
        <p className="mt-1 text-xs text-navy-light">{STYLE_LABELS[style].hint}</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1 flex items-center justify-between text-sm font-medium">
            <span>Pace</span>
            <span className="text-navy-light">{pace.toFixed(2)}×</span>
          </label>
          <input
            type="range"
            min={0.5}
            max={2.0}
            step={0.05}
            value={pace}
            onChange={(e) => onPaceChange(Number(e.target.value))}
            className="w-full accent-gold"
          />
        </div>

        {pitchAvailable && (
          <div>
            <label className="mb-1 flex items-center justify-between text-sm font-medium">
              <span>Pitch</span>
              <span className="text-navy-light">{(pitch ?? 0).toFixed(2)}</span>
            </label>
            <input
              type="range"
              min={-0.75}
              max={0.75}
              step={0.05}
              value={pitch ?? 0}
              onChange={(e) => onPitchChange(Number(e.target.value))}
              className="w-full accent-gold"
            />
          </div>
        )}
      </div>
      {!pitchAvailable && speaker && (
        <p className="text-xs text-navy-light">
          Pitch control isn&apos;t available for this voice (only a smaller set of voices support it).
        </p>
      )}
    </div>
  );
}
