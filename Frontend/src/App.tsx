import { useEffect, useRef, useState } from "react";

import { ChatInput } from "./components/ChatInput";
import { DocumentsPanel } from "./components/DocumentsPanel";
import { MessageBubble } from "./components/MessageBubble";
import { Sidebar } from "./components/Sidebar";
import { WelcomeScreen } from "./components/WelcomeScreen";
import { useChat } from "./hooks/useChat";
import { useConfig } from "./hooks/useConfig";

export default function App() {
  const chat = useChat();
  const { messages, isStreaming } = chat;
  const config = useConfig();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [documentsOpen, setDocumentsOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastContent = messages.at(-1)?.content;
  const attachedCount = messages.reduce((n, m) => n + (m.attachments?.length ?? 0), 0);

  // Keep the newest message in view while it streams, unless the user scrolled up to read.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom || !isStreaming) el.scrollTo({ top: el.scrollHeight });
  }, [messages.length, lastContent, isStreaming]);

  // Jump straight to the latest message when switching conversations.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "instant" });
  }, [chat.activeId]);

  return (
    <div className="layout">
      <Sidebar
        conversations={chat.conversations}
        activeId={chat.activeId}
        open={sidebarOpen}
        onNewChat={chat.newChat}
        onSelect={chat.selectConversation}
        onDelete={chat.deleteConversation}
        onClearAll={chat.clearHistory}
        onClose={() => setSidebarOpen(false)}
      />

      <div className="app">
        <header className="header">
          <div className="brand">
            <button
              type="button"
              className="menu-button"
              onClick={() => setSidebarOpen(true)}
              aria-label="Show previous chats"
            >
              ☰
            </button>
            <div className="brand-logo" aria-hidden="true">
              HR
            </div>
            <div>
              <h1>HR Assistant</h1>
              <p className="subtitle">Instant answers to your HR questions, straight from company policies.</p>
            </div>
          </div>
          <button type="button" className="ghost-button" onClick={() => setDocumentsOpen(true)}>
            📄 Documents
          </button>
        </header>

        <main className="chat" ref={scrollRef}>
          <div className="chat-inner" role="log" aria-live="polite" aria-busy={isStreaming}>
            {messages.length === 0 ? (
              <WelcomeScreen onPick={chat.send} />
            ) : (
              messages.map((m, i) => (
                <MessageBubble
                  key={m.id}
                  message={m}
                  isLast={i === messages.length - 1}
                  onRetry={chat.retry}
                  question={
                    m.role === "assistant" && messages[i - 1]?.role === "user" ? messages[i - 1].content : undefined
                  }
                  hrEmail={config.hr_contact_email || undefined}
                  onRate={config.feedback_enabled ? chat.rate : undefined}
                />
              ))
            )}
          </div>
        </main>

        <footer className="footer">
          <ChatInput
            disabled={isStreaming}
            isStreaming={isStreaming}
            attachmentsEnabled={config.attachments_enabled}
            maxAttachmentMb={config.max_attachment_mb}
            remainingAttachments={config.max_attachments_per_message - attachedCount}
            onSend={chat.send}
            onStop={chat.stop}
          />
        </footer>
      </div>

      {documentsOpen && <DocumentsPanel config={config} onClose={() => setDocumentsOpen(false)} />}
    </div>
  );
}
