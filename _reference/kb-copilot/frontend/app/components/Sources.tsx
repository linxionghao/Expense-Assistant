"use client";

import { useState } from "react";
import type { SourceItem } from "../types";

function isHttpUrl(x: string) {
  return /^https?:\/\//i.test(x);
}

export default function Sources({ sources }: { sources: SourceItem[] }) {
  const [open, setOpen] = useState(false);

  if (!sources?.length) return null;

  return (
    <div className="mt-4 border border-gray-800 rounded-2xl bg-gray-950/60">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-5 py-4 text-sm text-gray-200"
      >
        <span>Citations ({sources.length})</span>
        <span className="text-gray-500">{open ? "Hide" : "Show"}</span>
      </button>

      {open && (
        <div className="px-5 pb-5 space-y-4">
          {sources.map((s, idx) => (
            <div
              key={s.chunk_id}
              className="p-4 rounded-xl border border-gray-800 bg-black/20"
            >
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  {isHttpUrl(s.source) ? (
                    <a
                      href={s.source}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="text-sm font-medium text-gray-100 underline decoration-gray-600 hover:decoration-gray-300 break-words"
                      title={s.source}
                    >
                      [{idx + 1}] {s.title}
                    </a>
                  ) : (
                    <div className="text-sm font-medium text-gray-100 break-words">
                      [{idx + 1}] {s.title}
                    </div>
                  )}

                  <div className="mt-1 text-xs text-gray-400 break-all">
                    {isHttpUrl(s.source) ? (
                      <a
                        href={s.source}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="underline decoration-gray-700 hover:decoration-gray-400"
                      >
                        {s.source}
                      </a>
                    ) : (
                      s.source
                    )}
                  </div>
                </div>

                {isHttpUrl(s.source) && (
                  <a
                    href={s.source}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="shrink-0 text-xs px-2 py-1 rounded-lg border border-gray-700 text-gray-300 hover:text-white hover:border-gray-500"
                  >
                    Open
                  </a>
                )}
              </div>

              <div className="mt-3 text-sm text-gray-200 leading-relaxed">
                {s.snippet}
              </div>

              <div className="mt-1 text-[11px] text-gray-600">
                chunk_id: {s.chunk_id}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}