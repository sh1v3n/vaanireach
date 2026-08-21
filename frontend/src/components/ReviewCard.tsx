"use client";

import { useState } from "react";
import { motion } from "framer-motion";

import { API_BASE_URL, approveLanguage, editScene, rejectLanguage, regenerateLanguage } from "@/lib/api-client";
import type { LanguageCode, LanguageJobView } from "@/types";

const LANGUAGE_LABELS: Record<LanguageCode, string> = {
  en: "English", hi: "हिन्दी (Hindi)", mr: "मराठी (Marathi)", bn: "বাংলা (Bengali)",
  ta: "தமிழ் (Tamil)", te: "తెలుగు (Telugu)", kn: "ಕನ್ನಡ (Kannada)", ml: "മലയാളം (Malayalam)",
  gu: "ગુજરાતી (Gujarati)",
};

const AVATAR_LABELS: Record<number, string> = {
  1: "✅ real lip-sync (Hedra)",
  2: "✅ real lip-sync (D-ID)",
  3: "⚠️ placeholder (no lip-sync)",
};

export function ReviewCard({
  jobId,
  language,
  view,
  onChanged,
}: {
  jobId: string;
  language: LanguageCode;
  view: LanguageJobView;
  onChanged: () => void;
}) {
  const [editingSceneId, setEditingSceneId] = useState<string | null>(null);
  const [draftText, setDraftText] = useState("");
  const [editFeedback, setEditFeedback] = useState<{ isBlocking: boolean; explanation: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const avatarLabel = view.avatar_composited
    ? AVATAR_LABELS[view.avatar_tier ?? 3] ?? "⚠️ unknown"
    : "⚠️ degraded (plain B-roll)";

  async function handleSaveEdit(sceneId: string) {
    setBusy(true);
    try {
      const result = await editScene(jobId, language, sceneId, draftText);
      setEditFeedback({ isBlocking: result.verification.is_blocking, explanation: result.verification.explanation });
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function handleApprove() {
    setBusy(true);
    try {
      await approveLanguage(jobId, language);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function handleReject() {
    setBusy(true);
    try {
      await rejectLanguage(jobId, language);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  async function handleRegenerate() {
    setBusy(true);
    try {
      await regenerateLanguage(jobId, language);
      onChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="rounded-xl border border-navy-light/20 bg-white p-6 text-navy-dark shadow"
    >
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-serifDisplay text-xl">{LANGUAGE_LABELS[language]}</h3>
        <div className="flex gap-2 text-xs">
          <span className="rounded-full bg-navy/10 px-3 py-1">{avatarLabel}</span>
          <span className="rounded-full bg-gold/20 px-3 py-1 capitalize">{view.status.replace("_", " ")}</span>
        </div>
      </div>

      <video controls src={`${API_BASE_URL}${view.video_url}`} className="mb-4 w-full rounded-lg" />

      <div className="mb-4 flex gap-6 text-sm">
        <span>Facts verified: {view.verified_count}/{view.scenes.length}</span>
        <span>Blocking issues: {view.blocking_count}</span>
        <a href={`${API_BASE_URL}${view.srt_url}`} className="text-gold-dark underline">SRT</a>
        <a href={`${API_BASE_URL}${view.vtt_url}`} className="text-gold-dark underline">VTT</a>
      </div>

      <div className="mb-4 space-y-3">
        <h4 className="text-sm font-semibold">Script</h4>
        {view.scenes.map((scene) => {
          const vr = view.verification_results.find((r) => scene.claim_ids.includes(r.claim_id));
          return (
            <div key={scene.id} className="rounded border border-navy-light/10 p-3">
              <div className="mb-1 flex items-center justify-between">
                <span className="text-xs uppercase tracking-wide text-navy-light">{scene.narrative_role}</span>
                {vr && (
                  <span className={`text-xs ${vr.is_blocking ? "text-red-600" : "text-green-700"}`} title={vr.explanation}>
                    {vr.is_blocking ? "⚠️ flagged" : "✅ verified"}
                  </span>
                )}
              </div>
              {editingSceneId === scene.id && view.status === "pending_review" ? (
                <div>
                  <textarea
                    value={draftText}
                    onChange={(e) => setDraftText(e.target.value)}
                    rows={2}
                    className="w-full rounded border border-navy-light/30 p-2 text-sm"
                  />
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => handleSaveEdit(scene.id)}
                      className="rounded bg-gold px-3 py-1 text-xs font-medium text-navy-dark"
                    >
                      Save & re-verify
                    </button>
                    <button
                      type="button"
                      onClick={() => { setEditingSceneId(null); setEditFeedback(null); }}
                      className="rounded border border-navy-light/30 px-3 py-1 text-xs"
                    >
                      Cancel
                    </button>
                  </div>
                  {editFeedback && (
                    <p className={`mt-2 text-xs ${editFeedback.isBlocking ? "text-red-600" : "text-green-700"}`}>
                      {editFeedback.isBlocking ? "⚠️ " : "✅ "}{editFeedback.explanation}
                    </p>
                  )}
                </div>
              ) : (
                <div className="flex items-start justify-between gap-2">
                  <p className="text-sm">{scene.narration_segment_text}</p>
                  {view.status === "pending_review" && !view.regenerating && (
                    <button
                      type="button"
                      onClick={() => { setEditingSceneId(scene.id); setDraftText(scene.narration_segment_text); setEditFeedback(null); }}
                      className="shrink-0 text-xs text-gold-dark underline"
                    >
                      Edit
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {view.status === "pending_review" && (
        <div className="flex gap-3">
          <button
            type="button"
            disabled={busy}
            onClick={handleApprove}
            className="rounded-full bg-green-700 px-5 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Approve
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={handleReject}
            className="rounded-full bg-red-700 px-5 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            Reject
          </button>
          <button
            type="button"
            disabled={busy || view.regenerating}
            onClick={handleRegenerate}
            className="rounded-full border border-navy-light/30 px-5 py-2 text-sm font-medium disabled:opacity-50"
          >
            {view.regenerating ? "Regenerating…" : "Regenerate"}
          </button>
        </div>
      )}
      {view.error && (
        <p className="mt-2 text-xs text-red-600">⚠️ {view.error}</p>
      )}
    </motion.div>
  );
}
