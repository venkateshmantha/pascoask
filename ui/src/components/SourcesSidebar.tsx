"use client";

import type { SourceChunk } from "@/types";
import { SourceBadge } from "./SourceBadge";

interface Props {
  chunks: SourceChunk[];
  onPlainEnglish?: (chunk: SourceChunk) => void;
}

export function SourcesSidebar({ chunks, onPlainEnglish }: Props) {
  if (!chunks.length) {
    return (
      <div className="p-4 text-sm text-gray-400">
        Sources will appear here after you ask a question.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-3 overflow-y-auto">
      <h2 className="text-sm font-semibold text-gray-700">Sources ({chunks.length})</h2>
      {chunks.map((chunk, i) => (
        <div key={chunk.chunk_id} className="border rounded-lg p-3 text-xs bg-white shadow-sm">
          <div className="flex items-center gap-2 mb-1">
            <span className="text-gray-400 font-mono">{i + 1}.</span>
            <SourceBadge source={chunk.source} />
            <span className="text-gray-400">{chunk.date}</span>
            <span className="ml-auto text-gray-300">{chunk.score.toFixed(2)}</span>
          </div>
          <p className="text-gray-700 mb-2 line-clamp-3">{chunk.summary || chunk.text}</p>
          <div className="flex items-center gap-2 flex-wrap">
            <a
              href={chunk.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 hover:underline truncate max-w-[180px]"
            >
              View source →
            </a>
            {chunk.doc_type === "ordinance" && onPlainEnglish && (
              <button
                onClick={() => onPlainEnglish(chunk)}
                className="ml-auto text-green-700 border border-green-300 rounded px-2 py-0.5 hover:bg-green-50"
              >
                Plain English
              </button>
            )}
          </div>
          {chunk.entity_tags.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {chunk.entity_tags.slice(0, 4).map((tag) => (
                <span key={tag} className="bg-gray-100 text-gray-500 rounded px-1 py-0.5 text-[10px]">
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
