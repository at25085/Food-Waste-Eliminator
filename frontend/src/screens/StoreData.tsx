import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { postFile, postJSON, ApiError } from "../api";
import type { BatchUploadResult, DailyUploadResult, Store, StoreIn } from "../types";
import { AccessCode, Panel } from "../components/ui";
import StorePicker from "../components/StorePicker";
import { useToast } from "../components/toast";
import { day, placeName, int, isNum, reasonHint, reasonText, spct, wape } from "../format";

const enc = encodeURIComponent;

export default function StoreData(props: {
  stores: Store[];
  store: string | null;
  setStore: (s: string) => void;
  reloadStores: () => void;
}) {
  const { store, stores, setStore } = props;
  const info = stores.find((s) => s.store_id === store);

  // Select a newly created store only once the reloaded list contains it; selecting it earlier
  // would make App fall back to the first store.
  const [pendingSelect, setPendingSelect] = useState<string | null>(null);
  useEffect(() => {
    if (pendingSelect && stores.some((s) => s.store_id === pendingSelect)) {
      setStore(pendingSelect);
      setPendingSelect(null);
    }
  }, [pendingSelect, stores, setStore]);

  return (
    <div className="screen">
      <header className="screen__head">
        <div>
          <h1 className="display">Upload</h1>
          <p className="screen__lede">{info ? placeName(info) : "Pick a store"}</p>
        </div>
        <StorePicker stores={stores} value={store} onChange={setStore} />
      </header>

      <CreateStore
        onCreated={(id) => {
          props.reloadStores();
          setPendingSelect(id);
        }}
      />

      <Panel
        title="Upload daily sheet"
        sub={
          <>
            One row per product per day: received, sold, wasted, price, discount, shelf life. Delivery sheets with expiry dates
            are recognised automatically.{" "}
            <a className="link" href="/api/uploads/template.csv" download>
              Download template
            </a>
          </>
        }
      >
        <SheetUpload<SheetResult>
          store={store}
          endpoint={store ? `/api/stores/${enc(store)}/sheets` : null}
          label="Daily sheet"
          onDone={() => undefined}
          render={(r) => (r.sheet === "batch" ? <BatchResult r={r} store={store!} /> : <DailyResult r={r} store={store!} />)}
        />
      </Panel>
    </div>
  );
}

type SheetResult = ({ sheet: "daily" } & DailyUploadResult) | ({ sheet: "batch" } & BatchUploadResult);

// ---------- Create store ----------

const EMPTY_STORE = { store_id: "", city: "" };

function CreateStore({ onCreated }: { onCreated: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState(EMPTY_STORE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);
  const toast = useToast();
  const set = (k: keyof typeof EMPTY_STORE) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));

  const idOk = /^[A-Za-z0-9_\- ]{1,40}$/.test(f.store_id.trim());
  const ready = idOk && f.city.trim().length > 1;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    const body: StoreIn = { store_id: f.store_id.trim(), city: f.city.trim(), kind: "owner" };
    try {
      const r = await postJSON<{ store_id: string; city: string }>("/api/stores", body);
      toast({ kind: "info", title: `${r.store_id} added`, body: `Located in ${r.city}. Upload its daily sheet next.` });
      setF(EMPTY_STORE);
      setOpen(false);
      onCreated(r.store_id);
    } catch (err) {
      if ((err as ApiError).status === 401) setLocked(true);
      else setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title="Add a store"
      aside={
        <button className={`btn btn--sm ${open ? "btn--ghost" : ""}`} onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? "Cancel" : "New store"}
        </button>
      }
      className={open ? undefined : "panel--collapsed"}
    >
      {open && locked && <AccessCode onSaved={() => setLocked(false)} />}
      {open && (
        <form className="formgrid formgrid--store" onSubmit={submit}>
          <Field label="Store name">
            <input className="input" required value={f.store_id} onChange={set("store_id")} placeholder="Midtown" maxLength={40} />
          </Field>
          <Field label="City" hint="Add the state if the name is common, e.g. Augusta, GA">
            <input className="input" required value={f.city} onChange={set("city")} placeholder="Atlanta" />
          </Field>
          <div className="formgrid__actions">
            <button className="btn btn--primary btn--sm" type="submit" disabled={!ready || busy}>
              {busy ? "Adding…" : "Add store"}
            </button>
            {error && (
              <span className="upload__err" role="alert">
                {error}
              </span>
            )}
          </div>
        </form>
      )}
    </Panel>
  );
}

/** A labelled form field. `group` for controls that aren't a single input (a <label> around
 *  several buttons would click the first one when its text is clicked). */
function Field({ label, hint, group, children }: { label: string; hint?: string; group?: boolean; children: ReactNode }) {
  const inner = (
    <>
      <span className="field__l">{label}</span>
      {children}
      {hint && <span className="field__hint">{hint}</span>}
    </>
  );
  return group ? (
    <div className="field" role="group" aria-label={label}>
      {inner}
    </div>
  ) : (
    <label className="field">{inner}</label>
  );
}

// ---------- Uploads ----------

function SheetUpload<R>(props: {
  store: string | null;
  endpoint: string | null;
  label: string;
  onDone: () => void;
  render: (r: R) => ReactNode;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<R | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  // A result belongs to the store it was uploaded to.
  useEffect(() => {
    setResult(null);
    setError(null);
  }, [props.store]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file || !props.endpoint) return;
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      setResult(await postFile<R>(props.endpoint, file));
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      props.onDone();
    } catch (err) {
      if ((err as ApiError).status === 401) setLocked(true);
      else setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
    {locked && <AccessCode onSaved={() => setLocked(false)} />}
    <form className="upload upload--flat" onSubmit={submit}>
      <label
        className={`dropzone ${dragging ? "is-drag" : ""} ${file ? "has-file" : ""} ${!props.endpoint ? "is-off" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          if (props.endpoint) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const f = e.dataTransfer.files?.[0];
          if (f && props.endpoint) setFile(f);
        }}
      >
        <svg className="dropzone__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden>
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
        </svg>
        <span className="dropzone__title">{file ? file.name : "Drag and drop your sheet here"}</span>
        <span className="dropzone__sub">
          {file ? `${Math.max(1, Math.round(file.size / 1024))} KB · click to choose another` : <>or <u>browse your files</u> · .csv or .xlsx, up to 5 MB</>}
        </span>
        <input
          ref={inputRef}
          type="file"
          className="dropzone__input"
          accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          aria-label={`${props.label} file (.csv or .xlsx)`}
          disabled={!props.endpoint}
        />
      </label>
      <div className="upload__row">
        <button className="btn btn--primary" type="submit" disabled={!file || busy || !props.endpoint}>
          {busy ? (
            <>
              <span className="spinner spinner--inline" aria-hidden /> Validating and forecasting…
            </>
          ) : (
            "Upload and validate"
          )}
        </button>
      </div>
      {error && (
        <p className="upload__err" role="alert">
          Upload failed: {error}
        </p>
      )}
      {result && props.render(result)}
    </form>
    </>
  );
}

function Counts({ rows, accepted, quarantined }: { rows: number; accepted: number; quarantined: number }) {
  return (
    <>
      <span className="upload__n">
        <strong>{int(rows)}</strong> rows
      </span>
      <span className="upload__n upload__n--ok">
        <strong>{int(accepted)}</strong> accepted
      </span>
      <span className={`upload__n ${quarantined ? "upload__n--bad" : ""}`}>
        <strong>{int(quarantined)}</strong> quarantined
      </span>
    </>
  );
}

function Reasons({ reasons }: { reasons: Record<string, number> }) {
  const list = Object.entries(reasons).sort((a, b) => b[1] - a[1]);
  if (!list.length) return null;
  return (
    <ul className="upload__reasons">
      {list.map(([code, n]) => (
        <li key={code} title={reasonHint(code) ?? code}>
          {int(n)} × {reasonText(code)}
          {reasonHint(code) && <span className="muted"> — {reasonHint(code)}</span>}
        </li>
      ))}
    </ul>
  );
}

function QuarantineLink({ store, uploadId }: { store: string; uploadId: string }) {
  return (
    <a className="link" href={`/api/stores/${enc(store)}/uploads/${enc(uploadId)}/quarantine.csv`} download>
      Rejected rows (CSV)
    </a>
  );
}

function DailyResult({ r, store }: { r: DailyUploadResult; store: string }) {
  const ev = r.evaluation;
  return (
    <div className="upload__result" role="status">
      <Counts rows={r.rows} accepted={r.accepted} quarantined={r.quarantined} />
      {r.quarantined > 0 && <QuarantineLink store={store} uploadId={r.upload_id} />}
      <Reasons reasons={r.reasons} />
      {r.accepted > 0 && (
        <ul className="upload__facts">
          <li>
            <strong>{int(r.outcomes_attached)}</strong> forecasts graded against what really sold
          </li>
          <li>
            {ev && isNum(ev.evaluated_days) && ev.evaluated_days > 0 ? (
              <>
                Model graded on <strong>{int(ev.evaluated_days)} days it never saw</strong>: forecast error (WAPE){" "}
                <strong>{wape(ev.wape)}</strong>, bias <strong>{spct(ev.bias, 1)}</strong>
                {ev.version && (
                  <>
                    {" "}
                    (<code className="ver">{ev.version}</code>)
                  </>
                )}
              </>
            ) : (
              <span className="muted">
                No new days to grade — the model already trained on these dates, or products need a week of history first.
              </span>
            )}
          </li>
          {r.next_forecast_date && (
            <li>
              Tomorrow's plan rebuilt from this store's real stock for <strong>{day(r.next_forecast_date)}</strong>.{" "}
              <a className="link" href="#/today">
                Open Today's plan
              </a>
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

function BatchResult({ r, store }: { r: BatchUploadResult; store: string }) {
  return (
    <div className="upload__result" role="status">
      <Counts rows={r.rows} accepted={r.accepted} quarantined={r.quarantined} />
      {r.quarantined > 0 && <QuarantineLink store={store} uploadId={r.upload_id} />}
      <Reasons reasons={r.reasons} />
      {r.accepted > 0 && (
        <p className="upload__facts">
          Real expiry dates now drive markdown timing and sell-through for these products.
        </p>
      )}
    </div>
  );
}
