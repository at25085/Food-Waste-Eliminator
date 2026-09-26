import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

export type ToastKind = "promoted" | "rejected" | "info" | "error";
interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  body?: string;
}

const Ctx = createContext<(t: Omit<Toast, "id">, ms?: number) => void>(() => {});

export function useToast() {
  return useContext(Ctx);
}

export function ToastHost({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => setToasts((ts) => ts.filter((t) => t.id !== id)), []);
  const push = useCallback(
    (t: Omit<Toast, "id">, ms = 5200) => {
      const id = nextId.current++;
      setToasts((ts) => [...ts.slice(-3), { ...t, id }]);
      window.setTimeout(() => dismiss(id), ms);
    },
    [dismiss],
  );

  return (
    <Ctx.Provider value={push}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast--${t.kind}`} role="status">
            <div className="toast__title">{t.title}</div>
            {t.body && <div className="toast__body">{t.body}</div>}
            <button className="toast__x" aria-label="Dismiss" onClick={() => dismiss(t.id)}>
              ×
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
