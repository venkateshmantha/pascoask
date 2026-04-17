"use client";

import { useRef, useEffect } from "react";
import type { Message } from "@/types";
import clsx from "clsx";

interface Props {
  messages: Message[];
  isLoading: boolean;
  input: string;
  onInputChange: (v: string) => void;
  onSubmit: (e: React.FormEvent) => void;
}

export function ChatPanel({ messages, isLoading, input, onInputChange, onSubmit }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="text-center text-gray-400 mt-16">
            <p className="text-lg font-medium mb-2">Ask PascoAsk</p>
            <p className="text-sm">
              Search Pasco County government records in plain English.
            </p>
            <div className="mt-4 space-y-2 text-left max-w-md mx-auto">
              {[
                "What are the setback rules for a residential fence?",
                "What did commissioners decide about the SR-54 corridor?",
                "What does MPUD zoning allow?",
              ].map((q) => (
                <button
                  key={q}
                  onClick={() => onInputChange(q)}
                  className="block w-full text-left border rounded-lg px-3 py-2 text-sm text-gray-600 hover:bg-gray-50"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            className={clsx(
              "flex",
              msg.role === "user" ? "justify-end" : "justify-start"
            )}
          >
            <div
              className={clsx(
                "max-w-2xl rounded-2xl px-4 py-3 text-sm whitespace-pre-wrap",
                msg.role === "user"
                  ? "bg-blue-600 text-white"
                  : "bg-white border shadow-sm text-gray-800"
              )}
            >
              {msg.content}
              {msg.sources && msg.sources.length > 0 && (
                <div className="mt-2 pt-2 border-t border-gray-100 text-xs text-gray-500">
                  {msg.sources.length} source{msg.sources.length > 1 ? "s" : ""} retrieved
                </div>
              )}
            </div>
          </div>
        ))}

        {isLoading && (
          <div className="flex justify-start">
            <div className="bg-white border shadow-sm rounded-2xl px-4 py-3 text-sm text-gray-400 animate-pulse">
              Searching records…
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form
        onSubmit={onSubmit}
        className="border-t p-3 flex gap-2 bg-white"
      >
        <input
          type="text"
          value={input}
          onChange={(e) => onInputChange(e.target.value)}
          placeholder="Ask a question about Pasco County records…"
          className="flex-1 border rounded-xl px-4 py-2 text-sm outline-none focus:ring-2 focus:ring-blue-300"
          disabled={isLoading}
        />
        <button
          type="submit"
          disabled={isLoading || !input.trim()}
          className="bg-blue-600 text-white rounded-xl px-4 py-2 text-sm font-medium disabled:opacity-50 hover:bg-blue-700"
        >
          Ask
        </button>
      </form>
    </div>
  );
}
