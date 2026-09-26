import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, postJSON } from "../api";

interface Turn {
  role: "user" | "assistant";
  text: string;
}

const SUGGESTIONS = [
  "What's most likely to go to waste tomorrow?",
  "Do I need any promotions today?",
  "What should I order the most of?",
  "How accurate have the forecasts been?",
];

/** Launcher in the bottom-right corner that opens a chat window about the selected store. */
export default function ChatWidget({ store, storeName }: { store: string | null; storeName: string | null }) {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setTurns([]);
    setError(null);
  }, [store]);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, [turns, busy]);

  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  async function ask(text: string) {
    const message = text.trim();
    if (!message || !store || busy) return;
    setDraft("");
    setError(null);
    const history = turns;
    setTurns([...history, { role: "user", text: message }]);
    setBusy(true);
    try {
      const r = await postJSON<{ reply: string }>(`/api/stores/${encodeURIComponent(store)}/chat`, { message, history });
      setTurns((t) => [...t, { role: "assistant", text: r.reply }]);
    } catch (e) {
      setError((e as ApiError).message);
      setTurns(history);
      setDraft(message);
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void ask(draft);
  }

  return (
    <div className="chatw">
      {open && (
        <section className="chatw__panel" role="dialog" aria-label="Ask about your store">
          <header className="chatw__head">
            <div>
              <div className="chatw__title">Ask about your store</div>
              <div className="chatw__store">{storeName ?? "No store selected"}</div>
            </div>
            <button className="chatw__close" onClick={() => setOpen(false)} aria-label="Close chat">
              <svg viewBox="0 0 24 24" aria-hidden>
                <path d="M6 6l12 12M18 6L6 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
            </button>
          </header>
          <div className="chatw__log" ref={logRef} aria-live="polite">
            <div className="chatw__msg chatw__msg--assistant">Hi! Ask me anything about this store's orders, waste or discounts.</div>
            {turns.length === 0 && (
              <div className="chatw__suggest">
                {SUGGESTIONS.map((s) => (
                  <button key={s} type="button" onClick={() => void ask(s)} disabled={!store || busy}>
                    {s}
                  </button>
                ))}
              </div>
            )}
            {turns.map((t, k) => (
              <div key={k} className={`chatw__msg chatw__msg--${t.role}`}>
                {t.text}
              </div>
            ))}
            {busy && (
              <div className="chatw__msg chatw__msg--assistant chatw__typing" aria-label="Thinking">
                <span />
                <span />
                <span />
              </div>
            )}
            {error && <div className="chatw__error">{error}</div>}
          </div>
          <form className="chatw__form" onSubmit={submit}>
            <input
              ref={inputRef}
              type="text"
              value={draft}
              maxLength={500}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Type a question…"
              aria-label="Question about your store"
              disabled={!store}
            />
            <button type="submit" disabled={!draft.trim() || busy || !store} aria-label="Send">
              <svg viewBox="0 0 24 24" aria-hidden>
                <path d="M4 12l16-8-6 16-2.5-6.5z" fill="currentColor" />
              </svg>
            </button>
          </form>
        </section>
      )}
      <button
        className={`chatw__launch ${open ? "is-open" : ""}`}
        onClick={() => setOpen((o) => !o)}
        aria-label={open ? "Close chat" : "Ask about your store"}
        aria-expanded={open}
      >
        {open ? (
          <svg viewBox="0 0 24 24" aria-hidden>
            <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" aria-hidden>
            <path d="M4 5h16v11H9l-5 4z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
            <circle cx="9" cy="10.5" r="1.2" fill="currentColor" />
            <circle cx="12" cy="10.5" r="1.2" fill="currentColor" />
            <circle cx="15" cy="10.5" r="1.2" fill="currentColor" />
          </svg>
        )}
      </button>
    </div>
  );
}
