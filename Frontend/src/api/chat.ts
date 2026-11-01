import { SseParser } from "./sse";
import type {
  AttachmentMeta,
  HistoryItem,
  PolicyDocument,
  PublicConfig,
  Rating,
  StreamEvent,
  UploadResult,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function errorFromResponse(response: Response): Promise<ApiError> {
  if (response.status === 429) {
    return new ApiError("You're sending messages too quickly. Please wait a moment and try again.", 429);
  }
  let detail = "Something went wrong. Please try again.";
  try {
    const body = await response.json();
    if (typeof body.detail === "string") detail = body.detail;
  } catch {
    // Non-JSON error body; keep the generic message.
  }
  return new ApiError(detail, response.status);
}

/** Streams a chat answer, invoking `onEvent` for each SSE event from the backend. */
export async function streamChat(
  message: string,
  history: HistoryItem[],
  onEvent: (event: StreamEvent) => void,
  signal: AbortSignal,
  attachmentIds: string[] = [],
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ message, history, attachment_ids: attachmentIds }),
    signal,
  });

  if (!response.ok || !response.body) throw await errorFromResponse(response);

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const event of parser.push(value)) onEvent(event);
  }
}

export async function getConfig(): Promise<PublicConfig> {
  const response = await fetch(`${API_BASE}/api/v1/config`);
  if (!response.ok) throw await errorFromResponse(response);
  return response.json();
}

export async function sendFeedback(feedback: {
  rating: Rating;
  question: string;
  answer: string;
  sources: string[];
  comment?: string;
}): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(feedback),
  });
  if (!response.ok) throw await errorFromResponse(response);
}

/** URL that opens a policy document; PDFs jump to the cited page when the section is "Page N". */
export function documentUrl(name: string, section?: string): string {
  const url = `${API_BASE}/api/v1/documents/${encodeURIComponent(name)}`;
  const page = section?.match(/^Page (\d+)$/)?.[1];
  return page && name.toLowerCase().endsWith(".pdf") ? `${url}#page=${page}` : url;
}

export async function listDocuments(): Promise<PolicyDocument[]> {
  const response = await fetch(`${API_BASE}/api/v1/documents`);
  if (!response.ok) throw await errorFromResponse(response);
  return response.json();
}

export async function checkAdminToken(token: string): Promise<boolean> {
  const response = await fetch(`${API_BASE}/api/v1/admin/check`, { headers: { "X-Admin-Token": token } });
  return response.ok;
}

export async function uploadDocuments(files: File[], token: string): Promise<UploadResult[]> {
  const form = new FormData();
  for (const file of files) form.append("files", file);
  const response = await fetch(`${API_BASE}/api/v1/admin/documents`, {
    method: "POST",
    headers: { "X-Admin-Token": token },
    body: form,
  });
  if (!response.ok) throw await errorFromResponse(response);
  return response.json();
}

export async function deleteDocument(name: string, token: string): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/admin/documents/${encodeURIComponent(name)}`, {
    method: "DELETE",
    headers: { "X-Admin-Token": token },
  });
  if (!response.ok) throw await errorFromResponse(response);
}

/** Uploads a file privately to the employee's conversation; the server checks it for sensitive data first. */
export async function uploadAttachment(file: File, signal?: AbortSignal): Promise<AttachmentMeta> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${API_BASE}/api/v1/attachments`, { method: "POST", body: form, signal });
  if (!response.ok) throw await errorFromResponse(response);
  return response.json();
}
