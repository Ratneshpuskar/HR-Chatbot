import type { Conversation } from "../api/types";
import { conversationTitle } from "../hooks/useChat";

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  open: boolean;
  onNewChat: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onClearAll: () => void;
  onClose: () => void;
}

const DAY = 24 * 60 * 60 * 1000;

function groupLabel(updatedAt: number, now: number): string {
  const startOfToday = new Date(now).setHours(0, 0, 0, 0);
  if (updatedAt >= startOfToday) return "Today";
  if (updatedAt >= startOfToday - DAY) return "Yesterday";
  if (updatedAt >= startOfToday - 7 * DAY) return "Previous 7 days";
  return "Older";
}

export function Sidebar({ conversations, activeId, open, onNewChat, onSelect, onDelete, onClearAll, onClose }: Props) {
  const now = Date.now();
  const groups: [string, Conversation[]][] = [];
  for (const c of conversations) {
    const label = groupLabel(c.updatedAt, now);
    const group = groups.at(-1);
    if (group?.[0] === label) group[1].push(c);
    else groups.push([label, [c]]);
  }

  const pick = (id: string) => {
    onSelect(id);
    onClose();
  };

  return (
    <>
      {open && <div className="sidebar-backdrop" onClick={onClose} aria-hidden="true" />}
      <aside className={`sidebar ${open ? "sidebar--open" : ""}`} aria-label="Previous chats">
        <button
          type="button"
          className="new-chat-button"
          onClick={() => {
            onNewChat();
            onClose();
          }}
        >
          + New chat
        </button>

        <nav className="history">
          {conversations.length === 0 ? (
            <p className="history-empty">Your previous chats will appear here.</p>
          ) : (
            groups.map(([label, items]) => (
              <section key={label}>
                <h2 className="history-label">{label}</h2>
                <ul>
                  {items.map((c) => (
                    <li key={c.id} className={`history-item ${c.id === activeId ? "history-item--active" : ""}`}>
                      <button
                        type="button"
                        className="history-open"
                        onClick={() => pick(c.id)}
                        aria-current={c.id === activeId ? "page" : undefined}
                        title={conversationTitle(c)}
                      >
                        {conversationTitle(c)}
                      </button>
                      <button
                        type="button"
                        className="history-delete"
                        onClick={() => onDelete(c.id)}
                        aria-label={`Delete chat: ${conversationTitle(c)}`}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))
          )}
        </nav>

        {conversations.length > 0 && (
          <div className="sidebar-footer">
            <button
              type="button"
              className="link-button"
              onClick={() => window.confirm("Delete all chat history from this browser?") && onClearAll()}
            >
              Clear history
            </button>
            <p>Chats are saved only in this browser for 30 days.</p>
          </div>
        )}
      </aside>
    </>
  );
}
