import type { ReactNode } from "react";
import { ApiError } from "../api";
import type { WasteRisk } from "../types";
import { weatherKind, weatherText } from "../format";

export function Panel(props: {
  title?: ReactNode;
  aside?: ReactNode;
  sub?: ReactNode;
  children: ReactNode;
  className?: string;
  flush?: boolean;
  id?: string;
}) {
  return (
    <section className={`panel ${props.flush ? "panel--flush" : ""} ${props.className ?? ""}`} id={props.id}>
      {(props.title || props.aside) && (
        <header className="panel__head">
          <div>
            {props.title && <h2 className="panel__title">{props.title}</h2>}
            {props.sub && <p className="panel__sub">{props.sub}</p>}
          </div>
          {props.aside && <div className="panel__aside">{props.aside}</div>}
        </header>
      )}
      <div className="panel__body">{props.children}</div>
    </section>
  );
}

/** One component for loading / error / empty so every endpoint fails the same, readable way. */
export function StateBlock(props: {
  loading?: boolean;
  error?: ApiError;
  empty?: boolean;
  notFound?: ReactNode; // what to say when the endpoint 404s (pipeline not run yet)
  emptyText?: ReactNode;
  onRetry?: () => void;
  compact?: boolean;
}) {
  const cls = `state ${props.compact ? "state--compact" : ""}`;
  if (props.error) {
    if (props.error.status === 404 && props.notFound) {
      return (
        <div className={`${cls} state--pending`} role="status">
          <PendingGlyph />
          <div>
            {props.notFound}
            <p className="state__detail">Server said: {props.error.message}</p>
            {props.onRetry && (
              <button className="btn btn--ghost" onClick={props.onRetry}>
                Check again
              </button>
            )}
          </div>
        </div>
      );
    }
    return (
      <div className={`${cls} state--error`} role="alert">
        <div>
          <strong>{props.error.status === 0 ? "API unreachable" : `Request failed (${props.error.status})`}</strong>
          <p className="state__detail">{props.error.message}</p>
          {props.onRetry && (
            <button className="btn btn--ghost" onClick={props.onRetry}>
              Try again
            </button>
          )}
        </div>
      </div>
    );
  }
  if (props.loading) {
    return (
      <div className={`${cls} state--loading`} role="status" aria-live="polite">
        <span className="spinner" aria-hidden /> Loading…
      </div>
    );
  }
  if (props.empty) return <div className={`${cls} state--empty`}>{props.emptyText ?? "Nothing here yet."}</div>;
  return null;
}

function PendingGlyph() {
  return (
    <svg className="state__glyph" viewBox="0 0 24 24" aria-hidden>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M12 7v5l3 2" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

const RISK_LABEL: Record<WasteRisk, string> = { high: "High", watch: "Watch", low: "Low" };
export function RiskBadge({ risk }: { risk: WasteRisk }) {
  return (
    <span className={`risk risk--${risk}`}>
      <span className="risk__dot" aria-hidden />
      {RISK_LABEL[risk] ?? risk}
    </span>
  );
}

/** The shelf-edge "reduced" sticker. Only markdowns wear sticker yellow. */
export function Sticker({ depth }: { depth: number }) {
  if (!depth) return <span className="muted">–</span>;
  return (
    <span className="sticker" title={`Suggested markdown: ${Math.round(depth * 100)}% off`}>
      −{Math.round(depth * 100)}%
    </span>
  );
}

export function DecisionChip({ decision }: { decision: string }) {
  const label = decision === "promoted" ? "Promoted" : decision === "rejected" ? "Rejected" : decision === "skipped" ? "Skipped" : decision;
  return (
    <span className={`chip chip--${decision}`}>
      <DecisionIcon decision={decision} />
      {label}
    </span>
  );
}

function DecisionIcon({ decision }: { decision: string }) {
  if (decision === "promoted")
    return (
      <svg viewBox="0 0 12 12" aria-hidden>
        <path d="M2.5 6.5l2.2 2.2L9.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    );
  if (decision === "rejected")
    return (
      <svg viewBox="0 0 12 12" aria-hidden>
        <path d="M3 3l6 6M9 3l-6 6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
    );
  return (
    <svg viewBox="0 0 12 12" aria-hidden>
      <path d="M2.5 6h7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

export function StatusChip({ status }: { status: string }) {
  const label = { champion: "Champion", retired: "Retired", rejected: "Rejected", candidate: "Candidate" }[status] ?? status;
  return <span className={`chip chip--status-${status}`}>{label}</span>;
}

export function Segmented<T extends string>(props: {
  value: T;
  options: { value: T; label: ReactNode; title?: string }[];
  onChange: (v: T) => void;
  label: string;
  size?: "sm" | "md";
}) {
  return (
    <div className={`seg seg--${props.size ?? "md"}`} role="radiogroup" aria-label={props.label}>
      {props.options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={props.value === o.value}
          className={`seg__opt ${props.value === o.value ? "is-on" : ""}`}
          onClick={() => props.onChange(o.value)}
          title={o.title}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Check({ ok, children }: { ok: boolean | null | undefined; children: ReactNode }) {
  return (
    <li className={`check ${ok ? "check--ok" : "check--fail"}`}>
      <span className="check__mark" aria-label={ok ? "passed" : "failed"}>
        {ok ? "✓" : "✗"}
      </span>
      <span>{children}</span>
    </li>
  );
}

export function WeatherGlyph({ code, size = 22 }: { code: number | null | undefined; size?: number }) {
  const k = weatherKind(code);
  const s = { width: size, height: size };
  const stroke = { fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
  const cloud = <path {...stroke} d="M7 17h9.5a3.5 3.5 0 0 0 .3-7 5 5 0 0 0-9.6 1.3A2.9 2.9 0 0 0 7 17z" />;
  return (
    <svg viewBox="0 0 24 24" style={s} className={`wx wx--${k}`} role="img" aria-label={weatherText(code)}>
      {k === "sun" && (
        <g {...stroke}>
          <circle cx="12" cy="12" r="4" />
          <path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4" />
        </g>
      )}
      {k === "partly" && (
        <g>
          <g {...stroke}>
            <circle cx="9" cy="9" r="3" />
            <path d="M9 3.5v1M3.5 9h1M5.1 5.1l.7.7M12.9 5.1l-.7.7" />
          </g>
          {cloud}
        </g>
      )}
      {(k === "cloud" || k === "unknown") && cloud}
      {k === "fog" && <path {...stroke} d="M4 9h16M6 13h12M4 17h16" />}
      {k === "rain" && (
        <g>
          <path {...stroke} d="M7 14h9.5a3.5 3.5 0 0 0 .3-7 5 5 0 0 0-9.6 1.3A2.9 2.9 0 0 0 7 14z" />
          <path {...stroke} d="M9 17l-1 3M13 17l-1 3M17 17l-1 3" />
        </g>
      )}
      {k === "snow" && (
        <g>
          <path {...stroke} d="M7 14h9.5a3.5 3.5 0 0 0 .3-7 5 5 0 0 0-9.6 1.3A2.9 2.9 0 0 0 7 14z" />
          <path {...stroke} d="M9 18h.01M13 19h.01M17 18h.01M11 21h.01M15 21h.01" strokeWidth={2.4} />
        </g>
      )}
      {k === "storm" && (
        <g>
          <path {...stroke} d="M7 14h9.5a3.5 3.5 0 0 0 .3-7 5 5 0 0 0-9.6 1.3A2.9 2.9 0 0 0 7 14z" />
          <path {...stroke} d="M12 15l-2 4h3l-2 3" />
        </g>
      )}
    </svg>
  );
}

export function Meter({ value, max, label }: { value: number; max: number; label: string }) {
  const frac = Math.max(0, Math.min(1, max ? value / max : 0));
  return (
    <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={max} aria-valuenow={value} aria-label={label}>
      <div className="meter__fill" style={{ width: `${frac * 100}%` }} />
    </div>
  );
}

/** Honesty label for a store's data ("Demo store — team-authored data", …). Renders nothing when
 *  the store has no label (the original Rohlik warehouses). */
export function StoreLabel({ store }: { store: { kind?: string | null; label?: string | null } | undefined }) {
  if (!store?.label) return null;
  return (
    <span className={`storelabel storelabel--${store.kind ?? "other"}`} title="Where this store's data comes from">
      {store.label}
    </span>
  );
}
