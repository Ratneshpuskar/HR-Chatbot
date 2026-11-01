import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

const MAX_CHARS = 4000;

interface Props {
  disabled: boolean;
  isStreaming: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}

export function ChatInput({ disabled, isStreaming, onSend, onStop }: Props) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Grow the textarea with its content, up to the CSS max-height.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
    el.style.overflowY = el.scrollHeight > el.clientHeight ? "auto" : "hidden";
  }, [value]);

  useEffect(() => {
    if (!isStreaming) textareaRef.current?.focus();
  }, [isStreaming]);

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (!value.trim() || disabled || value.length > MAX_CHARS) return;
    onSend(value);
    setValue("");
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const tooLong = value.length > MAX_CHARS;

  return (
    <form className="composer" onSubmit={submit}>
      <label htmlFor="chat-input" className="visually-hidden">
        Ask an HR question
      </label>
      <textarea
        id="chat-input"
        ref={textareaRef}
        rows={1}
        value={value}
        placeholder="Ask about leave, benefits, payroll, policies…"
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        aria-invalid={tooLong}
        aria-describedby="composer-hint"
      />
      {isStreaming ? (
        <button type="button" className="send-button send-button--stop" onClick={onStop} aria-label="Stop generating">
          ■
        </button>
      ) : (
        <button type="submit" className="send-button" disabled={disabled || !value.trim() || tooLong} aria-label="Send">
          ➤
        </button>
      )}
      <p id="composer-hint" className={`composer-hint ${tooLong ? "composer-hint--error" : ""}`}>
        {tooLong
          ? `Message is too long (${value.length}/${MAX_CHARS}).`
          : "Don't share ID numbers, bank/card details, or medical information. Enter to send, Shift+Enter for a new line."}
      </p>
    </form>
  );
}
