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
  feedback?: Rating;
  /** Files the employee attached to this message (metadata only; content stays on the server). */
  attachments?: AttachmentMeta[];
}

export interface AttachmentMeta {
  id: string;
  name: string;
  kind: "image" | "document";
  size_bytes: number;
  truncated: boolean;
}

export type Rating = 1 | -1;

export interface PublicConfig {
  company_name: string;
  hr_contact_email: string;
  feedback_enabled: boolean;
  admin_enabled: boolean;
  max_upload_mb: number;
  attachments_enabled: boolean;
  max_attachment_mb: number;
  max_attachments_per_message: number;
}

export interface Conversation {
  id: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
}

export interface PolicyDocument {
  name: string;
  title: string;
  size_bytes: number;
  updated_at: number;
  chunks: number;
  error: string | null;
}

export interface UploadResult {
  name: string;
  ok: boolean;
  message: string;
  chunks: number;
}

export interface HistoryItem {
  role: Role;
  content: string;
}

export type StreamEvent =
  | { event: "sources"; data: { sources: Source[]; blocked: boolean; cached: boolean } }
  | { event: "token"; data: { text: string } }
  | { event: "done"; data: Record<string, never> }
  | { event: "error"; data: { message: string; retryable: boolean } };
