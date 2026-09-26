import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { postFile, qs, useApi, ApiError } from "../api";
import type { AnomalyDay, DataSource, Store, TrafficUploadResult } from "../types";
import { Panel, StateBlock } from "./ui";
import StorePicker from "./StorePicker";
import { day, int } from "../format";

const REASONS: Record<string, string> = {
  blackout: "Blackout",
  mini_shutdown: "Partial shutdown",
  shutdown: "Store shutdown",
  frankfurt_shutdown: "Store shutdown",
};

export default function DataSources(props: {
  stores: Store[];
  store: string | null;
  setStore: (s: string) => void;
  onUploaded?: () => void;
}) {
  const { store } = props;
  const src = useApi<DataSource[]>(store ? `/api/data-sources${qs({ store })}` : null);
  const anomalies = useApi<AnomalyDay[]>(store ? `/api/anomaly-days${qs({ store })}` : null);
  const [uploadOpen, setUploadOpen] = useState(false);

  return (
    <Panel
      title="Data sources"
      sub="What each store's forecasts can see. Nothing is fabricated: a missing feed stays missing."
      aside={<StorePicker stores={props.stores} value={store} onChange={props.setStore} />}
    >
      {!src.data ? (
        <StateBlock loading={src.loading} error={src.error} onRetry={src.reload} />
      ) : (
        <ul className="sources">
          {src.data.map((s) => (
            <li key={s.key} className={`source ${s.connected ? "is-on" : "is-off"}`}>
              <span className="source__box" aria-label={s.connected ? "connected" : "not connected"}>
                {s.connected ? "✅" : "⬜"}
              </span>
              <div className="source__text">
                <div className="source__label">
                  {s.label}
                  {!s.connected && <span className="source__state">Not connected</span>}
                </div>
                <p className="source__detail">{s.detail}</p>
                {s.key === "traffic" && (
                  <div className="source__extra">
                    <span className="muted">
                      {int(s.first_party_rows ?? 0)} first-party traffic rows uploaded for {store?.replace("_", " ")}.
                    </span>
                    <button className={`btn ${s.connected ? "btn--ghost" : "btn--primary"} btn--sm`} onClick={() => setUploadOpen((o) => !o)} aria-expanded={uploadOpen}>
                      {s.connected ? "Upload traffic/POS CSV" : "Connect traffic/POS data"}
                    </button>
                  </div>
                )}
                {s.key === "traffic" && uploadOpen && (
                  <TrafficUpload
                    store={store}
                    onDone={() => {
                      src.reload();
                      props.onUploaded?.();
                    }}
                  />
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      <div className="subsection">
        <h3 className="h3">Unusual days</h3>
        <p className="note">
          Unusual days are flagged, never deleted. Days marked <em>exclude</em> stay in the history but aren't used as training
          targets; <em>keep</em> days train normally with the flag as context.
        </p>
        {!anomalies.data || !anomalies.data.length ? (
          <StateBlock
            compact
            loading={anomalies.loading}
            error={anomalies.error}
            onRetry={anomalies.reload}
            empty={!!anomalies.data}
            emptyText="No unusual days flagged for this store."
          />
        ) : (
          <table className="table table--compact">
            <thead>
              <tr>
                <th>Date</th>
                <th>Reason</th>
                <th>Training policy</th>
              </tr>
            </thead>
            <tbody>
              {anomalies.data.map((a) => (
                <tr key={a.date}>
                  <td>{day(a.date)}</td>
                  <td>{REASONS[a.reason ?? ""] ?? a.reason ?? "–"}</td>
                  <td>
                    <span className={`chip chip--policy-${a.training_policy}`}>
                      {a.training_policy === "exclude" ? "Exclude from training" : a.training_policy === "keep" ? "Keep" : a.training_policy}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Panel>
  );
}

function TrafficUpload({ store, onDone }: { store: string | null; onDone: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TrafficUploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const sampleHref = useMemo(() => {
    const s = store ?? "Prague_1";
    const rows = ["store_id,timestamp,customers_entered,transactions,units_sold,revenue"];
    const base = new Date();
    for (let i = 7; i >= 1; i--) {
      const d = new Date(base.getTime() - i * 86400000).toISOString().slice(0, 10);
      const c = 900 + Math.round(Math.sin(i) * 80) + i * 7;
      rows.push(`${s},${d}T20:00:00,${c},${Math.round(c * 0.82)},${Math.round(c * 3.1)},${(c * 21.4).toFixed(2)}`);
    }
    return URL.createObjectURL(new Blob([rows.join("\n") + "\n"], { type: "text/csv" }));
  }, [store]);
  useEffect(() => () => URL.revokeObjectURL(sampleHref), [sampleHref]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await postFile<TrafficUploadResult>("/api/traffic/csv", file);
      setResult(r);
      onDone();
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="upload" onSubmit={submit}>
      <p className="upload__cols">
        CSV columns: <code>store_id, timestamp, customers_entered, transactions, units_sold, revenue</code>. Rows are validated
        first; failing rows go to quarantine with the reason, never silently dropped.
      </p>
      <div className="upload__row">
        <input ref={fileRef} type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} aria-label="Traffic CSV file" />
        <button className="btn btn--primary btn--sm" type="submit" disabled={!file || busy}>
          {busy ? "Uploading…" : "Upload and validate"}
        </button>
        <a className="link" href={sampleHref} download={`traffic_sample_${store ?? "store"}.csv`}>
          Download sample CSV
        </a>
      </div>
      {error && (
        <p className="upload__err" role="alert">
          Upload failed: {error}
        </p>
      )}
      {result && (
        <div className="upload__result" role="status">
          <span className="upload__n upload__n--ok">
            <strong>{result.accepted}</strong> accepted
          </span>
          <span className={`upload__n ${result.quarantined ? "upload__n--bad" : ""}`}>
            <strong>{result.quarantined}</strong> quarantined
          </span>
          {Object.entries(result.reasons).length > 0 && (
            <ul className="upload__reasons">
              {Object.entries(result.reasons).map(([r, n]) => (
                <li key={r}>
                  {n} × <code>{r}</code>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </form>
  );
}
