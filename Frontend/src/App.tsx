import { useEffect, useRef } from "react";

import { ChatInput } from "./components/ChatInput";
import { MessageBubble } from "./components/MessageBubble";
import { WelcomeScreen } from "./components/WelcomeScreen";
import { useChat } from "./hooks/useChat";

export default function App() {
  const { messages, isStreaming, send, retry, stop, reset } = useChat();
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastContent = messages.at(-1)?.content;

  // Keep the newest message in view while it streams, unless the user scrolled up to read.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom || !isStreaming) el.scrollTo({ top: el.scrollHeight });
  }, [messages.length, lastContent, isStreaming]);

  return (
    <div className="app">
      <header className="header">
        <div className="brand">
          <div className="brand-logo" aria-hidden="true">
            HR
          </div>
          <div>
            <h1>HR Assistant</h1>
            <p className="subtitle">AI answers may be imperfect. Confirm important decisions with HR.</p>
          </div>
        </div>
        {messages.length > 0 && (
          <button type="button" className="ghost-button" onClick={reset}>
            New chat
          </button>
        )}
      </header>

      <main className="chat" ref={scrollRef}>
        <div className="chat-inner" role="log" aria-live="polite" aria-busy={isStreaming}>
          {messages.length === 0 ? (
            <WelcomeScreen onPick={send} />
          ) : (
            messages.map((m, i) => (
              <MessageBubble key={m.id} message={m} isLast={i === messages.length - 1} onRetry={retry} />
            ))
          )}
        </div>
      </main>

      <footer className="footer">
        <ChatInput disabled={isStreaming} isStreaming={isStreaming} onSend={send} onStop={stop} />
      </footer>
    </div>
  );
}
