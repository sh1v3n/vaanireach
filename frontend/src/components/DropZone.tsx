"use client";

import { useRef, useState } from "react";
import { motion } from "framer-motion";

const ACCEPTED_EXTENSIONS = [".txt", ".pdf"];

function isAcceptedFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext));
}

/**
 * Drag-and-drop upload zone. The whole zone is also a plain click target
 * (opens the native file picker) — dragging is never the ONLY way to
 * pick a file, per WCAG 2.2 AA's single-pointer-alternative requirement
 * for author-controlled drag interactions.
 */
export function DropZone({ file, onFileChange }: { file: File | null; onFileChange: (file: File | null) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [rejected, setRejected] = useState(false);

  function handleFiles(files: FileList | null) {
    const picked = files?.[0];
    if (!picked) return;
    if (!isAcceptedFile(picked)) {
      setRejected(true);
      return;
    }
    setRejected(false);
    onFileChange(picked);
  }

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        aria-label="Upload a government notice file — click to browse or drag and drop a .txt or .pdf file"
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragOver(true);
        }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setIsDragOver(false);
          handleFiles(e.dataTransfer.files);
        }}
        className={`cursor-pointer rounded-lg border-2 border-dashed p-8 text-center transition-colors ${
          isDragOver ? "border-gold bg-gold/10" : "border-navy-light/40 bg-navy/[0.02] hover:border-gold/60"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".txt,.pdf"
          className="sr-only"
          onChange={(e) => handleFiles(e.target.files)}
        />

        <motion.svg
          animate={isDragOver ? { y: -4 } : { y: 0 }}
          transition={{ duration: 0.2 }}
          className="mx-auto mb-3 h-10 w-10 text-navy-light"
          fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 8.25 12 3.75m0 0L7.5 8.25M12 3.75v13.5" />
        </motion.svg>

        {file ? (
          <div>
            <p className="font-medium text-navy-dark">{file.name}</p>
            <p className="mt-1 text-xs text-navy-light">{(file.size / 1024).toFixed(0)} KB — click or drop to replace</p>
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onFileChange(null); if (inputRef.current) inputRef.current.value = ""; }}
              className="mt-2 text-xs text-red-600 underline"
            >
              Remove
            </button>
          </div>
        ) : (
          <div>
            <p className="font-medium text-navy-dark">Drag &amp; drop a government notice here</p>
            <p className="mt-1 text-xs text-navy-light">or click to browse — .txt or .pdf</p>
          </div>
        )}
      </div>
      {rejected && (
        <p className="mt-2 text-xs text-red-600">Only .txt or .pdf files are supported.</p>
      )}
    </div>
  );
}
