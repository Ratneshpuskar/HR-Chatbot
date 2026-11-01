import { memo, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { ChatMessage } from "../api/types";

interface Props {
  message: ChatMessage;
  isLast: boolean;
  onRetry: () => void;
}

function MessageBubbleImpl({ message, isLast, onRetry }: Props) {
  const [copied, setCopied] = useState(false);
  const isUser = message.role === "user";
  const isStreaming = message.status === "streaming";

  const copy = async () => {
    await navigator.clipboard.writeText(message.content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  // Deduplicate sources by document + section for display.
  const sources = [...new Map((message.sources ?? []).map((s) => [`${s.source}#${s.section}`, s])).values()];

  return (
    <div className={`message ${isUser ? "message--user" : "message--assistant"}`}>
      {!isUser && (
        <div className="avatar" aria-hidden="true">
          HR
        </div>
      )}
      <div className="bubble-wrap">
        {(isUser || message.content || isStreaming) && (
          <div className={`bubble ${message.blocked ? "bubble--warning" : ""}`}>
            {isUser ? (
              <p className="user-text">{message.content}</p>
            ) : message.content ? (
              // react-markdown does not render raw HTML, so model output can't inject markup.
              <Markdown
                remarkPlugins={[remarkGfm]}
                components={{ a: (props) => <a {...props} target="_blank" rel="noopener noreferrer" /> }}
              >
                {message.content}
              </Markdown>
            ) : isStreaming ? (
              <span className="typing" aria-label="HR Assistant is typing">
                <span />
                <span />
                <span />
              </span>
            ) : null}
            {isStreaming && message.content && <span className="cursor" aria-hidden="true" />}
          </div>
        )}

        {message.status === "error" && (
          <div className="message-error" role="alert">
            <span>{message.error}</span>
            {isLast && (
              <button type="button" className="link-button" onClick={onRetry}>
                Retry
              </button>
            )}
          </div>
        )}

        {!isUser && message.status === "done" && !message.blocked && (
          <div className="message-meta">
            {sources.length > 0 && (
              <details className="sources">
                <summary>
                  {sources.length} source{sources.length > 1 ? "s" : ""}
                </summary>
                <ul>
                  {sources.map((s) => (
                    <li key={`${s.source}#${s.section}`}>
                      <strong>{s.title}</strong> — {s.section}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <button type="button" className="link-button" onClick={copy} aria-label="Copy answer">
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export const MessageBubble = memo(MessageBubbleImpl);
