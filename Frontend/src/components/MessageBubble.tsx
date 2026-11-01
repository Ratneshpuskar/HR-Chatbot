import { memo, useState, type FormEvent } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { documentUrl } from "../api/chat";
import type { ChatMessage, Rating } from "../api/types";

interface Props {
  message: ChatMessage;
  isLast: boolean;
  onRetry: () => void;
  /** The employee question this message answers (assistant messages only). */
  question?: string;
  hrEmail?: string;
  onRate?: (id: string, rating: Rating, comment?: string) => void;
}

function emailHrLink(hrEmail: string, question: string): string {
  const body = `Hi HR team,\n\nI asked the HR Assistant: "${question}"\n\nCould you help me with this?\n`;
  return `mailto:${hrEmail}?subject=${encodeURIComponent("Question for HR")}&body=${encodeURIComponent(body)}`;
}

function Feedback({ message, onRate }: { message: ChatMessage; onRate: NonNullable<Props["onRate"]> }) {
  const [askingWhy, setAskingWhy] = useState(false);
  const [comment, setComment] = useState("");

  if (message.feedback && !askingWhy) {
    return <span className="feedback-thanks">Thanks for the feedback</span>;
  }

  const submitComment = (e: FormEvent) => {
    e.preventDefault();
    onRate(message.id, -1, comment);
    setAskingWhy(false);
  };

  if (askingWhy) {
    return (
      <form className="feedback-form" onSubmit={submitComment}>
        <label htmlFor={`why-${message.id}`} className="visually-hidden">
          What was wrong with this answer?
        </label>
        <input
          id={`why-${message.id}`}
          value={comment}
          maxLength={1000}
          placeholder="What was missing or wrong? (optional)"
          onChange={(e) => setComment(e.target.value)}
          autoFocus
        />
        <button type="submit" className="link-button">
          Send
        </button>
      </form>
    );
  }

  return (
    <span className="feedback">
      <button type="button" className="icon-button" onClick={() => onRate(message.id, 1)} aria-label="Helpful">
        👍
      </button>
      <button type="button" className="icon-button" onClick={() => setAskingWhy(true)} aria-label="Not helpful">
        👎
      </button>
    </span>
  );
}

function MessageBubbleImpl({ message, isLast, onRetry, question, hrEmail, onRate }: Props) {
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
              <>
                {message.attachments && message.attachments.length > 0 && (
                  <ul className="message-attachments" aria-label="Attached files">
                    {message.attachments.map((a) => (
                      <li key={a.id}>
                        <span aria-hidden="true">{a.kind === "image" ? "🖼️" : "📄"}</span> {a.name}
                      </li>
                    ))}
                  </ul>
                )}
                <p className="user-text">{message.content}</p>
              </>
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
                      <a href={documentUrl(s.source, s.section)} target="_blank" rel="noopener noreferrer">
                        <strong>{s.title}</strong> — {s.section}
                      </a>
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <button type="button" className="link-button" onClick={copy} aria-label="Copy answer">
              {copied ? "Copied" : "Copy"}
            </button>
            {hrEmail && question && (
              <a className="link-button" href={emailHrLink(hrEmail, question)}>
                Email HR
              </a>
            )}
            {onRate && question && <Feedback message={message} onRate={onRate} />}
          </div>
        )}
      </div>
    </div>
  );
}

export const MessageBubble = memo(MessageBubbleImpl);
