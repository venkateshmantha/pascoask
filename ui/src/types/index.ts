export type DocType = "meeting_minutes" | "ordinance" | "zoning_code" | "staff_report";
export type Source = "bcc_minutes" | "ldc" | "civicclerk" | "gis";

export interface SourceChunk {
  chunk_id: string;
  document_id: string;
  source: Source;
  doc_type: DocType;
  text: string;
  summary: string;
  url: string;
  date: string;
  score: number;
  entity_tags: string[];
  citizen_question: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  sources?: SourceChunk[];
}

export type SearchMode = "hybrid" | "semantic";
