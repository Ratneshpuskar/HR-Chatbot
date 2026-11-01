import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, sendFeedback, streamChat } from "../api/chat";
import type { AttachmentMeta, ChatMessage, Conversation, HistoryItem, Rating } from "../api/types";

// Saved in this browser only: there's no login yet, so the server can't tell employees apart.
export const STORAGE_KEY = "hr-assistant:conversations:v1";
const MAX_CONVERSATIONS = 50;
const RETENTION_MS = 30 * 24 * 60 * 60 * 1000;
const MAX_HISTORY = 20;
export const REDACTED = "[Message removed because it contained sensitive personal data]";

function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed: Conversation[] = raw ? JSON.parse(raw) : [];
    const cutoff = Date.now() - RETENTION_MS;
    return parsed
      .filter((c) => c.updatedAt >= cutoff && c.messages.length > 0)
      .map((c) => ({
        ...c,
        // A stream can't survive a reload; mark any in-flight answer as interrupted.
        messages: c.messages.map((m) =>
          m.status === "streaming" ? { ...m, status: "error", error: "Interrupted. Please retry." } : m,
        ),
      }));
  } catch {
    return [];
  }
}

function saveConversations(conversations: Conversation[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations.slice(0, MAX_CONVERSATIONS)));
  } catch {
    // Storage full or disabled (private mode); history just won't persist.
  }
}

/** Sidebar title: the first question, which is already redacted if it contained sensitive data. */
export function conversationTitle(conversation: Conversation): string {
  const first = conversation.messages.find((m) => m.role === "user")?.content ?? "New chat";
  return first === REDACTED ? "Message removed" : first.length > 60 ? `${first.slice(0, 57)}…` : first;
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
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations);
  const [activeId, setActiveId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const active = conversations.find((c) => c.id === activeId) ?? null;
  const messages = useMemo(() => active?.messages ?? [], [active]);
  const isStreaming = messages.some((m) => m.status === "streaming");
  const anyStreaming = conversations.some((c) => c.messages.some((m) => m.status === "streaming"));

  const conversationsRef = useRef(conversations);
  conversationsRef.current = conversations;

  // Persist once streaming settles, rather than rewriting storage on every token.
  useEffect(() => {
    if (!anyStreaming) saveConversations(conversations);
  }, [conversations, anyStreaming]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const updateMessage = useCallback(
    (conversationId: string, messageId: string, patch: (m: ChatMessage) => Partial<ChatMessage>) => {
      setConversations((prev) =>
        prev.map((c) =>
          c.id !== conversationId
            ? c
            : { ...c, messages: c.messages.map((m) => (m.id === messageId ? { ...m, ...patch(m) } : m)) },
        ),
      );
    },
    [],
  );

  const run = useCallback(
    async (
      conversationId: string | null,
      text: string,
      priorMessages: ChatMessage[],
      attachments: AttachmentMeta[],
    ) => {
      const convId = conversationId ?? crypto.randomUUID();
      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: text,
        status: "done",
        ...(attachments.length ? { attachments } : {}),
      };
      // Files attached anywhere in the conversation stay available for follow-up questions.
      const attachmentIds = [...priorMessages, userMsg].flatMap((m) => m.attachments?.map((a) => a.id) ?? []);
      const botId = crypto.randomUUID();
      const botMsg: ChatMessage = { id: botId, role: "assistant", content: "", status: "streaming" };
      const now = Date.now();

      setConversations((prev) => {
        const existing = prev.find((c) => c.id === convId);
        const updated: Conversation = {
          id: convId,
          createdAt: existing?.createdAt ?? now,
          updatedAt: now,
          messages: [...priorMessages, userMsg, botMsg],
        };
        // Most recently used conversation first.
        return [updated, ...prev.filter((c) => c.id !== convId)];
      });
      setActiveId(convId);

      const controller = new AbortController();
      abortRef.current = controller;
      const update = (id: string, patch: (m: ChatMessage) => Partial<ChatMessage>) => updateMessage(convId, id, patch);

      try {
        await streamChat(
          text,
          toHistory(priorMessages),
          (event) => {
            switch (event.event) {
              case "sources":
                update(botId, () => ({ sources: event.data.sources, blocked: event.data.blocked }));
                if (event.data.blocked) update(userMsg.id, () => ({ content: REDACTED }));
                break;
              case "token":
                update(botId, (m) => ({ content: m.content + event.data.text }));
                break;
              case "done":
                update(botId, () => ({ status: "done" }));
                break;
              case "error":
                update(botId, () => ({ status: "error", error: event.data.message }));
                break;
            }
          },
          controller.signal,
          attachmentIds,
        );
        // Stream closed without a terminal event (e.g. proxy cut the connection).
        update(botId, (m) =>
          m.status === "streaming" ? { status: "error", error: "The connection was interrupted." } : {},
        );
      } catch (err) {
        if (controller.signal.aborted) {
          update(botId, (m) => ({ status: "done", content: m.content || "_Stopped._" }));
        } else {
          const message =
            err instanceof ApiError ? err.message : "Couldn't reach the HR Assistant. Check your connection.";
          update(botId, () => ({ status: "error", error: message }));
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [updateMessage],
  );

  const send = useCallback(
    (text: string, attachments: AttachmentMeta[] = []) => {
      const trimmed = text.trim();
      if (!trimmed || isStreaming) return;
      void run(activeId, trimmed, messages, attachments);
    },
    [activeId, isStreaming, messages, run],
  );

  const retry = useCallback(() => {
    if (isStreaming || !activeId) return;
    const lastUserIndex = messages.findLastIndex((m) => m.role === "user");
    if (lastUserIndex === -1 || messages[lastUserIndex].content === REDACTED) return;
    const last = messages[lastUserIndex];
    void run(activeId, last.content, messages.slice(0, lastUserIndex), last.attachments ?? []);
  }, [activeId, isStreaming, messages, run]);

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const newChat = useCallback(() => {
    abortRef.current?.abort();
    setActiveId(null);
  }, []);

  const selectConversation = useCallback((id: string) => {
    abortRef.current?.abort();
    setActiveId(id);
  }, []);

  const deleteConversation = useCallback(
    (id: string) => {
      if (id === activeId) {
        abortRef.current?.abort();
        setActiveId(null);
      }
      setConversations((prev) => prev.filter((c) => c.id !== id));
    },
    [activeId],
  );

  const clearHistory = useCallback(() => {
    abortRef.current?.abort();
    setActiveId(null);
    setConversations([]);
  }, []);

  /** Sends 👍/👎 for an answer so HR can see which answers need better policy content. */
  const rate = useCallback(
    async (messageId: string, rating: Rating, comment?: string) => {
      const conversation = conversationsRef.current.find((c) => c.messages.some((m) => m.id === messageId));
      if (!conversation) return;
      const index = conversation.messages.findIndex((m) => m.id === messageId);
      const answer = conversation.messages[index];
      const question = conversation.messages[index - 1];
      if (question?.role !== "user") return;
      updateMessage(conversation.id, messageId, () => ({ feedback: rating }));
      try {
        await sendFeedback({
          rating,
          question: question.content,
          answer: answer.content,
          sources: (answer.sources ?? []).map((s) => `${s.source}#${s.section}`),
          comment: comment?.trim() || undefined,
        });
      } catch {
        // Feedback is best-effort; never interrupt the conversation over it.
      }
    },
    [updateMessage],
  );

  return {
    conversations,
    activeId,
    messages,
    isStreaming,
    send,
    retry,
    stop,
    rate,
    newChat,
    selectConversation,
    deleteConversation,
    clearHistory,
  };
}
