"use client";

import { useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { AskApiResponse, ChatMessage, Role } from "./types";
import { isAskError, isRole } from "./types";
import Sources from "./components/Sources";

function uid() {
  return Math.random().toString(16).slice(2);
}

// Backend sometimes appends a line like: "Citations: [1], [2]".
// We keep inline [1] markers inside the answer, but remove this UI-noise line.
function stripCitationsLine(answer: string) {
  const lines = answer.split("\n");
  const cleaned = lines.filter((line) => {
    const t = line.trim();
    // Matches: Citations: [1]  OR  Citations: [1], [2], [3]
    return !/^Citations:\s*(\[\d+\])(\s*,\s*\[\d+\])*\s*$/i.test(t);
  });
  return cleaned.join("\n").trimEnd();
}

export default function Home() {
  const [role, setRole] = useState<Role>("engineering");
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: uid(),
      role: "assistant",
      content:
        "Ask me anything about AWS Well-Architected or Kubernetes docs.\n\nExample: \n**What are kubernetes objects?** , **What are the 6 pillars of AWS?** \n\n (Kubernetes docs are only available for engineering role, make sure you select it from the role dropdown above!)",
    },
  ]);

  const listRef = useRef<HTMLDivElement | null>(null);

  const canSend = useMemo(
    () => input.trim().length > 0 && !loading,
    [input, loading]
  );

  async function send() {
    const q = input.trim();
    if (!q || loading) return;

    setInput("");
    setLoading(true);

    // push user msg
    const userMsg: ChatMessage = { id: uid(), role: "user", content: q };
    setMessages((prev) => [...prev, userMsg]);

    // placeholder assistant msg (for typewriter)
    const assistantId = uid();
    setMessages((prev) => [
      ...prev,
      { id: assistantId, role: "assistant", content: "Thinking..." },
    ]);

    // auto scroll
    setTimeout(() => {
      listRef.current?.scrollTo({
        top: listRef.current.scrollHeight,
        behavior: "smooth",
      });
    }, 50);

    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: q,
          user: { role },
        }),
      });

      const data = (await res.json()) as AskApiResponse;

      if (!res.ok) {
        throw new Error(isAskError(data) ? data.error : "Request failed");
      }

      // ✅ data is AskResponse here
      const fullRaw = "answer" in data ? data.answer : "";
      const full = stripCitationsLine(fullRaw);
      const srcs = "sources" in data ? data.sources : [];

      // Fake-stream (typewriter) the answer
      let i = 0;

      const interval = setInterval(() => {
        i += Math.max(2, Math.floor(full.length / 120)); // speed
        const partial = full.slice(0, i);

        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: partial, sources: srcs }
              : m
          )
        );

        if (i >= full.length) {
          clearInterval(interval);
        }
      }, 20);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Unknown error";

      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? {
                ...m,
                content: `❌ Error: ${msg}`,
              }
            : m
        )
      );
    } finally {
      setLoading(false);
      setTimeout(() => {
        listRef.current?.scrollTo({
          top: listRef.current.scrollHeight,
          behavior: "smooth",
        });
      }, 50);
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter new line
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <main className="min-h-screen bg-gray-950 text-white">
      {/* Header */}
      <header className="border-b border-gray-900">
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center justify-between">
          <div>
            <div className="text-lg font-semibold">Enterprise KB Copilot</div>
            <div className="text-xs text-gray-400">
              FastAPI `/ask` + citations + RBAC role filtering
            </div>
          </div>

          <div className="flex items-center gap-2 text-sm">
            <span className="text-gray-400">Role</span>
            <select
              value={role}
              onChange={(e: React.ChangeEvent<HTMLSelectElement>) => {
                const v = e.target.value;
                if (isRole(v)) setRole(v);
              }}
              className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2"
            >
              <option value="public">public</option>
              <option value="engineering">engineering</option>
            </select>
          </div>
        </div>
      </header>

      {/* Chat */}
      <section className="max-w-4xl mx-auto px-6 py-6">
        <div
          ref={listRef}
          className="h-[70vh] overflow-auto rounded-2xl border border-gray-900 bg-black/20 p-4 space-y-4"
        >
          {messages.map((m) => (
            <div
              key={m.id}
              className={`flex ${
                m.role === "user" ? "justify-end" : "justify-start"
              }`}
            >
              <div
                className={`max-w-[80%] rounded-2xl px-4 py-3 border ${
                  m.role === "user"
                    ? "bg-white text-black border-white/20"
                    : "bg-gray-900 border-gray-800"
                }`}
              >
                <div className="text-sm leading-relaxed whitespace-pre-wrap">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {m.content}
                  </ReactMarkdown>
                </div>

                {"sources" in m && m.sources && m.sources.length > 0 && (
                  <Sources sources={m.sources} />
                )}
              </div>
            </div>
          ))}

          {loading && (
            <div className="flex justify-start">
              <div className="max-w-[80%] rounded-2xl px-4 py-3 border bg-gray-900 border-gray-800">
                <div className="animate-pulse space-y-2">
                  <div className="h-3 w-48 bg-gray-800 rounded"></div>
                  <div className="h-3 w-72 bg-gray-800 rounded"></div>
                  <div className="h-3 w-64 bg-gray-800 rounded"></div>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Input bar */}
        <div className="mt-4 rounded-2xl border border-gray-900 bg-gray-950 p-3 flex gap-3">
          <textarea
            value={input}
            onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
              setInput(e.target.value)
            }
            onKeyDown={onKeyDown}
            placeholder="Type your question... (Enter to send, Shift+Enter for new line)"
            className="flex-1 min-h-[52px] max-h-[140px] bg-transparent outline-none resize-none text-sm"
          />
          <button
            onClick={send}
            disabled={!canSend}
            className="px-4 py-2 rounded-xl bg-white text-black font-medium disabled:opacity-40"
          >
            {loading ? "..." : "Send"}
          </button>
        </div>
      </section>
    </main>
  );
}
