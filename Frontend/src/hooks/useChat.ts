import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, streamChat } from "../api/chat";
import type { ChatMessage, HistoryItem } from "../api/types";

// sessionStorage (not localStorage): conversations are cleared when the tab closes.
export const STORAGE_KEY = "hr-assistant:conversation";
const MAX_HISTORY = 20;
const REDACTED = "[Message removed because it contained sensitive personal data]";

function loadMessages(): ChatMessage[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    const parsed: ChatMessage[] = raw ? JSON.parse(raw) : [];
    // A stream can't survive a reload; mark any in-flight answer as interrupted.
    return parsed.map((m) =>
      m.status === "streaming" ? { ...m, status: "error", error: "Interrupted. Please retry." } : m,
    );
  } catch {
    return [];
  }
}

function toHistory(messages: ChatMessage[]): HistoryItem[] {
  const history: HistoryItem[] = [];
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    const reply = m.role === "user" ? messages[i + 1] : undefined;
    // Only send completed, non-blocked exchanges back to the server.
    if (m.role === "user" && reply?.status === "done" && !reply.blocked) {
      history.push({ role: "user", content: m.content }, { role: "assistant", content: reply.content });
    }
  }
  return history.slice(-MAX_HISTORY);
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>(loadMessages);
  const abortRef = useRef<AbortController | null>(null);
  const isStreaming = messages.some((m) => m.status === "streaming");

  useEffect(() => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
  }, [messages]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const updateMessage = useCallback((id: string, patch: (m: ChatMessage) => Partial<ChatMessage>) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch(m) } : m)));
  }, []);

  const run = useCallback(
    async (text: string, priorMessages: ChatMessage[]) => {
      const userMsg: ChatMessage = { id: crypto.randomUUID(), role: "user", content: text, status: "done" };
      const botId = crypto.randomUUID();
      const botMsg: ChatMessage = { id: botId, role: "assistant", content: "", status: "streaming" };
      setMessages([...priorMessages, userMsg, botMsg]);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        await streamChat(
          text,
          toHistory(priorMessages),
          (event) => {
            switch (event.event) {
              case "sources":
                updateMessage(botId, () => ({ sources: event.data.sources, blocked: event.data.blocked }));
                if (event.data.blocked) updateMessage(userMsg.id, () => ({ content: REDACTED }));
                break;
              case "token":
                updateMessage(botId, (m) => ({ content: m.content + event.data.text }));
                break;
              case "done":
                updateMessage(botId, () => ({ status: "done" }));
                break;
              case "error":
                updateMessage(botId, () => ({ status: "error", error: event.data.message }));
                break;
            }
          },
          controller.signal,
        );
        // Stream closed without a terminal event (e.g. proxy cut the connection).
        updateMessage(botId, (m) =>
          m.status === "streaming" ? { status: "error", error: "The connection was interrupted." } : {},
        );
      } catch (err) {
        if (controller.signal.aborted) {
          updateMessage(botId, (m) => ({ status: "done", content: m.content || "_Stopped._" }));
        } else {
          const message =
            err instanceof ApiError ? err.message : "Couldn't reach the HR Assistant. Check your connection.";
          updateMessage(botId, () => ({ status: "error", error: message }));
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [updateMessage],
  );

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || isStreaming) return;
      void run(trimmed, messages);
    },
    [isStreaming, messages, run],
  );

  const retry = useCallback(() => {
    if (isStreaming) return;
    const lastUserIndex = messages.findLastIndex((m) => m.role === "user");
    if (lastUserIndex === -1 || messages[lastUserIndex].content === REDACTED) return;
    void run(messages[lastUserIndex].content, messages.slice(0, lastUserIndex));
  }, [isStreaming, messages, run]);

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setMessages([]);
  }, []);

  return { messages, isStreaming, send, retry, stop, reset };
}
