"use client";

import type { LanguageCode } from "@/types";

const LANGUAGE_LABELS: Record<LanguageCode, string> = {
  en: "English",
  hi: "हिन्दी (Hindi)",
  mr: "मराठी (Marathi)",
  bn: "বাংলা (Bengali)",
  ta: "தமிழ் (Tamil)",
  te: "తెలుగు (Telugu)",
  kn: "ಕನ್ನಡ (Kannada)",
  ml: "മലയാളം (Malayalam)",
  gu: "ગુજરાતી (Gujarati)",
};

export function LanguagePicker({
  selected,
  onChange,
}: {
  selected: LanguageCode[];
  onChange: (languages: LanguageCode[]) => void;
}) {
  function toggle(lang: LanguageCode) {
    onChange(selected.includes(lang) ? selected.filter((l) => l !== lang) : [...selected, lang]);
  }

  return (
    <div className="flex flex-wrap gap-2">
      {(Object.keys(LANGUAGE_LABELS) as LanguageCode[]).map((lang) => (
        <button
          key={lang}
          type="button"
          onClick={() => toggle(lang)}
          className={`rounded-full border px-4 py-1.5 text-sm transition-colors ${
            selected.includes(lang)
              ? "border-gold bg-gold text-navy-dark font-medium"
              : "border-navy-light bg-transparent text-navy hover:border-gold"
          }`}
        >
          {LANGUAGE_LABELS[lang]}
        </button>
      ))}
    </div>
  );
}
