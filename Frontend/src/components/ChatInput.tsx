import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { uploadAttachment } from "../api/chat";
import type { AttachmentMeta } from "../api/types";

const MAX_CHARS = 4000;
const ACCEPT = ".png,.jpg,.jpeg,.webp,.pdf,.docx,.txt,.md";
const DEFAULT_ATTACHMENT_QUESTION =
  "Please summarise the attached file and point out anything relevant to our HR policies.";

interface PendingAttachment {
  key: string;
  name: string;
  status: "uploading" | "ready" | "error";
  meta?: AttachmentMeta;
  error?: string;
}

interface Props {
  disabled: boolean;
  isStreaming: boolean;
  attachmentsEnabled: boolean;
  maxAttachmentMb: number;
  /** How many more files this conversation can take. */
  remainingAttachments: number;
  onSend: (text: string, attachments: AttachmentMeta[]) => void;
  onStop: () => void;
}

export function ChatInput({
  disabled,
  isStreaming,
  attachmentsEnabled,
  maxAttachmentMb,
  remainingAttachments,
  onSend,
  onStop,
}: Props) {
  const [value, setValue] = useState("");
  const [pending, setPending] = useState<PendingAttachment[]>([]);
  const [dragging, setDragging] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Grow the textarea with typed content, from the CSS one-line height (matching the buttons) up
  // to its max-height; only then show a scrollbar. An empty box always stays one line, because
  // browsers count the placeholder in scrollHeight and it may wrap while the layout settles.
  const resize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "";
    el.style.overflowY = "hidden";
    if (!el.value) return;
    const borders = el.offsetHeight - el.clientHeight;
    const max = parseFloat(getComputedStyle(el).maxHeight) || Infinity;
    const target = el.scrollHeight + borders;
    if (target > el.offsetHeight) el.style.height = `${Math.min(target, max)}px`;
    if (target > max) el.style.overflowY = "auto";
  }, []);

  useLayoutEffect(resize, [value, resize]);

  // Text re-wraps when the width changes (window resize, sidebar, attach button appearing).
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    let width = el.clientWidth;
    const observer = new ResizeObserver(() => {
      if (el.clientWidth !== width) {
        width = el.clientWidth;
        resize();
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [resize]);

  useEffect(() => {
    if (!isStreaming) textareaRef.current?.focus();
  }, [isStreaming]);

  const ready = pending.filter((p) => p.status === "ready" && p.meta).map((p) => p.meta!);
  const uploading = pending.some((p) => p.status === "uploading");
  const slotsLeft = remainingAttachments - pending.filter((p) => p.status !== "error").length;

  const addFiles = (files: File[]) => {
    if (!attachmentsEnabled || files.length === 0) return;
    const accepted = files.slice(0, Math.max(slotsLeft, 0));
    const skipped = files.length - accepted.length;
    const entries: PendingAttachment[] = accepted.map((f) => ({
      key: crypto.randomUUID(),
      name: f.name || "pasted-image.png",
      status: "uploading",
    }));
    if (skipped > 0) {
      entries.push({
        key: crypto.randomUUID(),
        name: `${skipped} file${skipped > 1 ? "s" : ""}`,
        status: "error",
        error: "Attachment limit reached for this conversation.",
      });
    }
    setPending((prev) => [...prev, ...entries]);

    accepted.forEach((file, i) => {
      const { key } = entries[i];
      const update = (patch: Partial<PendingAttachment>) =>
        setPending((prev) => prev.map((p) => (p.key === key ? { ...p, ...patch } : p)));
      if (file.size > maxAttachmentMb * 2 ** 20) {
        update({ status: "error", error: `Larger than ${maxAttachmentMb} MB.` });
        return;
      }
      uploadAttachment(file)
        .then((meta) => update({ status: "ready", meta, name: meta.name }))
        .catch((err: unknown) =>
          update({ status: "error", error: err instanceof Error ? err.message : "Upload failed." }),
        );
    });
  };

  const removePending = (key: string) => setPending((prev) => prev.filter((p) => p.key !== key));

  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (disabled || uploading || value.length > MAX_CHARS) return;
    const text = value.trim() || (ready.length ? DEFAULT_ATTACHMENT_QUESTION : "");
    if (!text) return;
    onSend(text, ready);
    setValue("");
    setPending([]);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  // Screenshots can be pasted straight into the box.
  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData.files);
    if (files.length && attachmentsEnabled) {
      e.preventDefault();
      addFiles(files);
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    addFiles(Array.from(e.dataTransfer.files));
  };

  const tooLong = value.length > MAX_CHARS;
  const canSend = !disabled && !uploading && !tooLong && (value.trim().length > 0 || ready.length > 0);

  return (
    <form
      className={`composer ${dragging ? "composer--dragging" : ""}`}
      onSubmit={submit}
      onDragOver={(e) => {
        if (!attachmentsEnabled) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
      }}
      onDrop={onDrop}
    >
      {pending.length > 0 && (
        <ul className="attachment-chips" aria-label="Attached files">
          {pending.map((p) => (
            <li key={p.key} className={`chip chip--${p.status}`} title={p.error ?? p.name}>
              <span className="chip-icon" aria-hidden="true">
                {p.status === "uploading" ? "⏳" : p.status === "error" ? "⚠️" : p.meta?.kind === "image" ? "🖼️" : "📄"}
              </span>
              <span className="chip-name">{p.name}</span>
              {p.status === "uploading" && <span className="chip-note">Checking…</span>}
              {p.status === "error" && <span className="chip-note">{p.error}</span>}
              {p.meta?.truncated && <span className="chip-note">Long file: only the beginning is used</span>}
              <button
                type="button"
                className="chip-remove"
                onClick={() => removePending(p.key)}
                aria-label={`Remove ${p.name}`}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      {attachmentsEnabled && (
        <>
          <button
            type="button"
            className="attach-button"
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled || slotsLeft <= 0}
            aria-label="Attach a file"
            title={slotsLeft > 0 ? "Attach an image, PDF, Word or text file" : "Attachment limit reached"}
          >
            <svg
              viewBox="0 0 24 24"
              width="20"
              height="20"
              aria-hidden="true"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M21.4 11.1 12.2 20.3a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5" />
            </svg>
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPT}
            multiple
            hidden
            onChange={(e) => {
              addFiles(Array.from(e.target.files ?? []));
              e.target.value = "";
            }}
          />
        </>
      )}

      <label htmlFor="chat-input" className="visually-hidden">
        Ask an HR question
      </label>
      <textarea
        id="chat-input"
        ref={textareaRef}
        rows={1}
        value={value}
        placeholder={ready.length ? "Ask a question about your file…" : "Ask about leave, benefits, payroll, policies…"}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={onKeyDown}
        onPaste={onPaste}
        aria-invalid={tooLong}
        aria-describedby="composer-hint"
      />
      {isStreaming ? (
        <button type="button" className="send-button send-button--stop" onClick={onStop} aria-label="Stop generating">
          ■
        </button>
      ) : (
        <button type="submit" className="send-button" disabled={!canSend} aria-label="Send">
          ➤
        </button>
      )}
      <p id="composer-hint" className={`composer-hint ${tooLong ? "composer-hint--error" : ""}`}>
        {tooLong
          ? `Message is too long (${value.length}/${MAX_CHARS}).`
          : attachmentsEnabled
            ? "Attach images, PDFs or Word files with the paperclip, drag & drop, or paste."
            : "Don't share ID numbers, bank/card details, or medical information. Enter to send, Shift+Enter for a new line."}
      </p>
    </form>
  );
}
