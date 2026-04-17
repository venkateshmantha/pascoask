"use client";

import { useState, useCallback } from "react";
import type { Message, SearchMode, SourceChunk } from "@/types";
import { ChatPanel } from "@/components/ChatPanel";
import { SourcesSidebar } from "@/components/SourcesSidebar";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [searchMode, setSearchMode] = useState<SearchMode>("hybrid");
  const [activeSources, setActiveSources] = useState<SourceChunk[]>([]);
  const [plainEnglishModal, setPlainEnglishModal] = useState<{
    chunk: SourceChunk;
    text: string;
  } | null>(null);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const question = input.trim();
      if (!question || isLoading) return;

      const userMsg: Message = {
        id: Date.now().toString(),
        role: "user",
        content: question,
      };
      setMessages((prev) => [...prev, userMsg]);
      setInput("");
      setIsLoading(true);
      setActiveSources([]);

      const assistantId = (Date.now() + 1).toString();
      let assistantContent = "";
      let retrievedSources: SourceChunk[] = [];

      try {
        const response = await fetch(`${API_URL}/chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: question }),
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const reader = response.body!.getReader();
        const decoder = new TextDecoder();

        setMessages((prev) => [
          ...prev,
          { id: assistantId, role: "assistant", content: "" },
        ]);

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          const text = decoder.decode(value);
          const lines = text.split("\n").filter((l) => l.startsWith("data: "));

          for (const line of lines) {
            try {
              const event = JSON.parse(line.slice(6));
              if (event.type === "sources") {
                retrievedSources = event.chunks;
                setActiveSources(event.chunks);
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantId ? { ...m, sources: event.chunks } : m
                  )
                );
              } else if (event.type === "token") {
                assistantContent += event.content;
                setMessages((prev) =>
                  prev.map((m) =>
                    m.id === assistantId ? { ...m, content: assistantContent } : m
                  )
                );
              }
            } catch {}
          }
        }
      } catch (err) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: `Error: ${err instanceof Error ? err.message : "Unknown error"}` }
              : m
          )
        );
      } finally {
        setIsLoading(false);
      }
    },
    [input, isLoading]
  );

  const handlePlainEnglish = useCallback(async (chunk: SourceChunk) => {
    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: `Translate this legal/government text into 3 plain sentences a regular resident can understand:\n\n${chunk.text}`,
        }),
      });
      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let plainText = "";

      setPlainEnglishModal({ chunk, text: "Loading…" });

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const raw = decoder.decode(value);
        for (const line of raw.split("\n").filter((l) => l.startsWith("data: "))) {
          try {
            const ev = JSON.parse(line.slice(6));
            if (ev.type === "token") {
              plainText += ev.content;
              setPlainEnglishModal({ chunk, text: plainText });
            }
          } catch {}
        }
      }
    } catch (err) {
      setPlainEnglishModal({
        chunk,
        text: `Error: ${err instanceof Error ? err.message : "Unknown error"}`,
      });
    }
  }, []);

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-[#003087] text-white px-6 py-3 flex items-center justify-between shadow">
        <div>
          <span className="font-bold text-lg">PascoAsk</span>
          <span className="ml-2 text-blue-200 text-sm">Pasco County, FL Government Records</span>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className="text-blue-200">Search mode:</span>
          {(["hybrid", "semantic"] as SearchMode[]).map((mode) => (
            <button
              key={mode}
              onClick={() => setSearchMode(mode)}
              className={`px-3 py-1 rounded-full capitalize transition-colors ${
                searchMode === mode
                  ? "bg-white text-[#003087] font-medium"
                  : "border border-blue-400 text-blue-200 hover:bg-blue-800"
              }`}
            >
              {mode}
            </button>
          ))}
        </div>
      </header>

      {/* Main three-panel layout */}
      <div className="flex flex-1 overflow-hidden">
        {/* Chat panel */}
        <main className="flex-1 flex flex-col min-w-0 border-r">
          <ChatPanel
            messages={messages}
            isLoading={isLoading}
            input={input}
            onInputChange={setInput}
            onSubmit={handleSubmit}
          />
        </main>

        {/* Sources sidebar */}
        <aside className="w-80 flex flex-col border-l bg-gray-50 overflow-hidden">
          <SourcesSidebar
            chunks={activeSources}
            onPlainEnglish={handlePlainEnglish}
          />
        </aside>
      </div>

      {/* Plain English modal */}
      {plainEnglishModal && (
        <div
          className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
          onClick={() => setPlainEnglishModal(null)}
        >
          <div
            className="bg-white rounded-2xl shadow-xl p-6 max-w-lg w-full mx-4"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="font-semibold text-lg mb-3 text-[#00704A]">Plain English Summary</h3>
            <p className="text-sm text-gray-700 whitespace-pre-wrap mb-4">
              {plainEnglishModal.text}
            </p>
            <a
              href={plainEnglishModal.chunk.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-blue-600 hover:underline"
            >
              View original source →
            </a>
            <button
              onClick={() => setPlainEnglishModal(null)}
              className="mt-4 block ml-auto text-sm text-gray-500 hover:text-gray-800"
            >
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
