import { SseParser } from "./sse";
import type { HistoryItem, StreamEvent } from "./types";

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
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/v1/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ message, history }),
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
