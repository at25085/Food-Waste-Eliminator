import type { Store } from "../types";

/** A chain's stores fit on one row, so show them all instead of hiding them in a dropdown. */
export default function StorePicker(props: {
  stores: Store[];
  value: string | null;
  onChange: (id: string) => void;
  allowAll?: boolean;
}) {
  const { stores, value, onChange } = props;
  if (!stores.length) return <div className="storepick storepick--empty">Loading stores…</div>;
  return (
    <div className="storepick" role="radiogroup" aria-label="Store">
      {props.allowAll && (
        <button role="radio" aria-checked={!value} className={`storepick__opt ${!value ? "is-on" : ""}`} onClick={() => onChange("")}>
          <span className="storepick__id">All stores</span>
        </button>
      )}
      {stores.map((s) => {
        return (
          <button
            key={s.store_id}
            role="radio"
            aria-checked={value === s.store_id}
            className={`storepick__opt ${value === s.store_id ? "is-on" : ""}`}
            onClick={() => onChange(s.store_id)}
            title={`${s.store_id} (${s.city})${s.label ? ` — ${s.label}` : ""}`}
          >
            <span className="storepick__id">{s.store_id.replace(/_/g, " ")}</span>
          </button>
        );
      })}
    </div>
  );
}
