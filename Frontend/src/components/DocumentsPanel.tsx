import { useCallback, useEffect, useRef, useState, type DragEvent, type FormEvent } from "react";

import { checkAdminToken, deleteDocument, documentUrl, listDocuments, uploadDocuments } from "../api/chat";
import type { PolicyDocument, PublicConfig, UploadResult } from "../api/types";

const ACCEPT = ".md,.markdown,.txt,.pdf,.docx";
// The admin token lives only for this tab session.
const TOKEN_KEY = "hr-assistant:admin-token";

interface Props {
  config: PublicConfig;
  onClose: () => void;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function fileType(name: string): string {
  return name.split(".").pop()?.toUpperCase() ?? "";
}

export function DocumentsPanel({ config, onClose }: Props) {
  const [documents, setDocuments] = useState<PolicyDocument[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [token, setToken] = useState(() => sessionStorage.getItem(TOKEN_KEY) ?? "");
  const [tokenInput, setTokenInput] = useState("");
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<UploadResult[]>([]);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      setDocuments(await listDocuments());
      setLoadError(null);
    } catch {
      setLoadError("Couldn't load the document list.");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    dialogRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const unlock = async (e: FormEvent) => {
    e.preventDefault();
    setTokenError(null);
    if (await checkAdminToken(tokenInput.trim())) {
      sessionStorage.setItem(TOKEN_KEY, tokenInput.trim());
      setToken(tokenInput.trim());
      setTokenInput("");
    } else {
      setTokenError("That admin token isn't valid.");
    }
  };

  const lock = () => {
    sessionStorage.removeItem(TOKEN_KEY);
    setToken("");
    setResults([]);
  };

  const addFiles = (list: FileList | null) => {
    if (list) setFiles((prev) => [...prev, ...Array.from(list)]);
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    addFiles(e.dataTransfer.files);
  };

  const upload = async () => {
    if (!files.length) return;
    setBusy(true);
    setResults([]);
    try {
      setResults(await uploadDocuments(files, token));
      setFiles([]);
      await refresh();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Upload failed.";
      if (message.toLowerCase().includes("admin token")) lock();
      setResults([{ name: "Upload", ok: false, message, chunks: 0 }]);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (doc: PolicyDocument) => {
    if (!window.confirm(`Delete "${doc.name}"? The assistant will stop using it immediately.`)) return;
    setBusy(true);
    try {
      await deleteDocument(doc.name, token);
      await refresh();
    } catch (err) {
      setResults([
        { name: doc.name, ok: false, message: err instanceof Error ? err.message : "Delete failed.", chunks: 0 },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const isAdmin = Boolean(token);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="documents-title"
        tabIndex={-1}
        ref={dialogRef}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="modal-header">
          <div>
            <h2 id="documents-title">Policy documents</h2>
            <p className="modal-subtitle">The assistant answers only from these documents.</p>
          </div>
          <button type="button" className="ghost-button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <div className="modal-body">
          {loadError && <p className="message-error">{loadError}</p>}
          {documents === null && !loadError && <p className="muted">Loading…</p>}
          {documents?.length === 0 && <p className="muted">No documents yet.</p>}
          {documents && documents.length > 0 && (
            <ul className="doc-list">
              {documents.map((doc) => (
                <li key={doc.name} className="doc-item">
                  <span className="doc-type">{fileType(doc.name)}</span>
                  <div className="doc-info">
                    <a href={documentUrl(doc.name)} target="_blank" rel="noopener noreferrer" className="doc-title">
                      {doc.title}
                    </a>
                    <span className="doc-meta">
                      {doc.name} · {formatSize(doc.size_bytes)} · updated{" "}
                      {new Date(doc.updated_at * 1000).toLocaleDateString()}
                      {doc.error ? "" : ` · ${doc.chunks} section${doc.chunks === 1 ? "" : "s"}`}
                    </span>
                    {doc.error && <span className="doc-error">Not used: {doc.error}</span>}
                  </div>
                  {isAdmin && (
                    <button
                      type="button"
                      className="link-button danger"
                      onClick={() => remove(doc)}
                      disabled={busy}
                      aria-label={`Delete ${doc.name}`}
                    >
                      Delete
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}

          {config.admin_enabled && !isAdmin && (
            <form className="admin-unlock" onSubmit={unlock}>
              <label htmlFor="admin-token">HR admin? Enter your admin token to upload or delete documents.</label>
              <div className="admin-unlock-row">
                <input
                  id="admin-token"
                  type="password"
                  autoComplete="off"
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  placeholder="Admin token"
                />
                <button type="submit" className="ghost-button" disabled={!tokenInput.trim()}>
                  Unlock
                </button>
              </div>
              {tokenError && <p className="message-error">{tokenError}</p>}
            </form>
          )}

          {isAdmin && (
            <section className="upload" aria-label="Upload documents">
              <div className="upload-header">
                <h3>Upload documents</h3>
                <button type="button" className="link-button" onClick={lock}>
                  Lock
                </button>
              </div>
              <div
                className={`dropzone ${dragging ? "dropzone--active" : ""}`}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
                onClick={() => fileInputRef.current?.click()}
                onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && fileInputRef.current?.click()}
                role="button"
                tabIndex={0}
              >
                <p>
                  <strong>Drop files here</strong> or click to choose
                </p>
                <p className="muted">
                  PDF, Word (.docx), Markdown or text · up to {config.max_upload_mb} MB each · same name replaces the
                  existing document
                </p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={ACCEPT}
                  multiple
                  hidden
                  onChange={(e) => {
                    addFiles(e.target.files);
                    e.target.value = "";
                  }}
                />
              </div>

              {files.length > 0 && (
                <ul className="pending-files">
                  {files.map((f, i) => (
                    <li key={`${f.name}-${i}`}>
                      {f.name} <span className="muted">({formatSize(f.size)})</span>
                      <button
                        type="button"
                        className="link-button"
                        onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                        aria-label={`Remove ${f.name}`}
                      >
                        remove
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              <button type="button" className="primary-button" onClick={upload} disabled={busy || files.length === 0}>
                {busy ? "Uploading and indexing…" : `Upload ${files.length || ""} file${files.length === 1 ? "" : "s"}`}
              </button>
            </section>
          )}

          {results.length > 0 && (
            <ul className="upload-results" aria-live="polite">
              {results.map((r, i) => (
                <li key={`${r.name}-${i}`} className={r.ok ? "result-ok" : "result-error"}>
                  {r.ok ? "✓" : "✗"} <strong>{r.name}</strong>: {r.message}
                  {r.ok && ` Indexed ${r.chunks} section${r.chunks === 1 ? "" : "s"}.`}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
