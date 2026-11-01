export type Role = "user" | "assistant";

export interface Source {
  title: string;
  section: string;
  source: string;
  score: number;
}

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  sources?: Source[];
  status: "streaming" | "done" | "error";
  blocked?: boolean;
  error?: string;
}

export interface HistoryItem {
  role: Role;
  content: string;
}

export type StreamEvent =
  | { event: "sources"; data: { sources: Source[]; blocked: boolean } }
  | { event: "token"; data: { text: string } }
  | { event: "done"; data: Record<string, never> }
  | { event: "error"; data: { message: string; retryable: boolean } };
