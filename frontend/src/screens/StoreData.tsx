import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { postFile, postJSON, useApi, ApiError } from "../api";
import type {
  BatchUploadResult,
  DailyUploadResult,
  LearningStatus,
  RetrainJob,
  SellThroughRow,
  Store,
  StoreIn,
  UploadRecord,
} from "../types";
import { DecisionChip, Meter, Panel, Segmented, StateBlock, StoreLabel } from "../components/ui";
import StorePicker from "../components/StorePicker";
import { useToast } from "../components/toast";
import { day, int, isNum, num, pct, reasonHint, reasonText, shortDay, spct, stamp, wape } from "../format";

/** Mirrors store_learning.EVAL_DAYS: the most recent days a challenger is judged on and never trains on. */
const EVAL_DAYS = 14;

const enc = encodeURIComponent;

export default function StoreData(props: {
  stores: Store[];
  store: string | null;
  setStore: (s: string) => void;
  reloadStores: () => void;
}) {
  const { store, stores, setStore } = props;
  const info = stores.find((s) => s.store_id === store);
  const learning = useApi<LearningStatus>(store ? `/api/stores/${enc(store)}/learning` : null);
  const uploads = useApi<UploadRecord[]>(store ? `/api/stores/${enc(store)}/uploads` : null);
  const L = learning.data && learning.data.store_id === store ? learning.data : undefined;

  // Select a newly created store only once the reloaded list contains it; selecting it earlier
  // would make App fall back to the first store.
  const [pendingSelect, setPendingSelect] = useState<string | null>(null);
  useEffect(() => {
    if (pendingSelect && stores.some((s) => s.store_id === pendingSelect)) {
      setStore(pendingSelect);
      setPendingSelect(null);
    }
  }, [pendingSelect, stores, setStore]);

  const refresh = () => {
    learning.reload();
    uploads.reload();
  };

  const isRohlik = !!info && (!info.kind || info.kind === "rohlik");

  return (
    <div className="screen">
      <header className="screen__head">
        <div>
          <h1 className="display">Store data</h1>
          <p className="screen__lede">
            {info ? (
              <>
                {info.store_id.replace("_", " ")}, {info.city} <StoreLabel store={info} />
              </>
            ) : (
              "Pick a store"
            )}
          </p>
        </div>
        <StorePicker stores={stores} value={store} onChange={setStore} />
      </header>

      <p className="honesty">
        <span className="honesty__tag">Where this data comes from</span>
        {info?.label
          ? `${info.label}.`
          : "Rohlik warehouse: real sales history; inventory and waste are simulated until the store uploads its own sheets."}{" "}
        Every uploaded row is validated before anything learns from it; rejected rows are kept with the reason, never
        silently dropped or repaired.
      </p>

      <CreateStore
        onCreated={(id) => {
          props.reloadStores();
          setPendingSelect(id);
        }}
      />

      <div className="grid-2">
        <Panel
          title="Upload daily sheet"
          sub={
            <>
              Required format: one row per product per day: received, sold, wasted, closing stock, shelf life.{" "}
              <a className="link" href="/api/uploads/template.csv" download>
                Download daily template
              </a>
            </>
          }
        >
          {isRohlik && <RohlikNote />}
          <SheetUpload<DailyUploadResult>
            store={store}
            endpoint={store ? `/api/stores/${enc(store)}/uploads` : null}
            label="Daily sheet"
            onDone={refresh}
            render={(r) => <DailyResult r={r} store={store!} />}
          />
        </Panel>

        <Panel
          title="Upload batch sheet (optional)"
          sub={
            <>
              One row per delivery with its expiry date — gives exact sell-through and real expiry dates for markdown planning.{" "}
              <a className="link" href="/api/uploads/batch_template.csv" download>
                Download batch template
              </a>
            </>
          }
        >
          {isRohlik && <RohlikNote />}
          <SheetUpload<BatchUploadResult>
            store={store}
            endpoint={store ? `/api/stores/${enc(store)}/batches` : null}
            label="Batch sheet"
            onDone={refresh}
            render={(r) => <BatchResult r={r} store={store!} />}
          />
        </Panel>
      </div>

      <LearningPanel store={store} learning={learning} L={L} />

      <div className="grid-2">
        <AdherencePanel L={L} learning={learning} />
        <DiscountPanel L={L} learning={learning} />
      </div>

      <SellThroughPanel store={store} L={L} learning={learning} />
      <UploadHistory store={store} uploads={uploads} />
    </div>
  );
}

function RohlikNote() {
  return (
    <p className="note note--top">
      This is a Rohlik warehouse with its own real history. To try your own sheets, add a store above and upload to it.
    </p>
  );
}

// ---------- Create store ----------

const EMPTY_STORE = {
  store_id: "",
  city: "",
  lat: "",
  lon: "",
  timezone: "America/New_York",
  country: "US",
  subdivision: "",
  kind: "demo" as "demo" | "owner",
};

function CreateStore({ onCreated }: { onCreated: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState(EMPTY_STORE);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const set = (k: keyof typeof EMPTY_STORE) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));

  const lat = parseFloat(f.lat);
  const lon = parseFloat(f.lon);
  const coordsOk = Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180;
  const idOk = /^[A-Za-z0-9_\- ]{1,40}$/.test(f.store_id.trim());
  const ready = idOk && f.city.trim() && coordsOk && f.timezone.trim() && f.country.trim();

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    const body: StoreIn = {
      store_id: f.store_id.trim(),
      city: f.city.trim(),
      lat,
      lon,
      timezone: f.timezone.trim(),
      country: f.country.trim(),
      subdivision: f.subdivision.trim() || null,
      kind: f.kind,
    };
    try {
      const r = await postJSON<{ store_id: string }>("/api/stores", body);
      toast({ kind: "info", title: `Store ${r.store_id} created`, body: "Now upload its daily sheet below." });
      setF(EMPTY_STORE);
      setOpen(false);
      onCreated(r.store_id);
    } catch (err) {
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title="Add a store"
      sub={open ? "Location drives the weather the model sees; the timezone decides what \"tomorrow\" means." : undefined}
      aside={
        <button className={`btn btn--sm ${open ? "btn--ghost" : ""}`} onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          {open ? "Cancel" : "New store"}
        </button>
      }
      className={open ? undefined : "panel--collapsed"}
    >
      {open && (
        <form className="formgrid" onSubmit={submit}>
          <Field label="Store id" hint="Letters, digits, _ or - (max 40)">
            <input className="input" required value={f.store_id} onChange={set("store_id")} placeholder="Atlanta_Midtown" maxLength={40} />
          </Field>
          <Field label="City">
            <input className="input" required value={f.city} onChange={set("city")} placeholder="Atlanta" />
          </Field>
          <Field label="Latitude">
            <input className="input" required type="number" step="any" min={-90} max={90} value={f.lat} onChange={set("lat")} placeholder="33.7756" />
          </Field>
          <Field label="Longitude">
            <input className="input" required type="number" step="any" min={-180} max={180} value={f.lon} onChange={set("lon")} placeholder="-84.3963" />
          </Field>
          <Field label="Timezone">
            <input className="input" required value={f.timezone} onChange={set("timezone")} />
          </Field>
          <Field label="Country">
            <input className="input" required value={f.country} onChange={set("country")} maxLength={2} />
          </Field>
          <Field label="State / subdivision" hint="Used for public holidays, e.g. GA">
            <input className="input" value={f.subdivision} onChange={set("subdivision")} placeholder="GA" />
          </Field>
          <Field group label="Whose data" hint="Demo = team-authored data; it will be labeled everywhere">
            <Segmented<"demo" | "owner">
              label="Whose data"
              size="sm"
              value={f.kind}
              onChange={(kind) => setF((x) => ({ ...x, kind }))}
              options={[
                { value: "demo", label: "Demo", title: "Team-authored data; labeled as a demo everywhere" },
                { value: "owner", label: "Owner", title: "A real store's own data" },
              ]}
            />
          </Field>
          <div className="formgrid__actions">
            <button className="btn btn--primary btn--sm" type="submit" disabled={!ready || busy}>
              {busy ? "Creating…" : "Create store"}
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
      setError((err as ApiError).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="upload upload--flat" onSubmit={submit}>
      <div className="upload__row">
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          aria-label={`${props.label} file (.csv or .xlsx)`}
          disabled={!props.endpoint}
        />
        <button className="btn btn--primary btn--sm" type="submit" disabled={!file || busy || !props.endpoint}>
          {busy ? "Validating…" : "Upload and validate"}
        </button>
      </div>
      {error && (
        <p className="upload__err" role="alert">
          Upload failed: {error}
        </p>
      )}
      {result && props.render(result)}
    </form>
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

// ---------- Learning status ----------

type LearningApi = ReturnType<typeof useApi<LearningStatus>>;

function LearningPanel({ store, learning, L }: { store: string | null; learning: LearningApi; L: LearningStatus | undefined }) {
  const toast = useToast();
  const [starting, setStarting] = useState(false);
  const job = L?.last_retrain ?? null;
  const running = job?.state === "running";

  // Poll while a challenger trains in the background.
  const { reload } = learning;
  useEffect(() => {
    if (!running) return;
    const t = window.setTimeout(reload, 5000);
    return () => window.clearTimeout(t);
  }, [running, L, reload]);

  // Announce the verdict when a run we watched finishes.
  const prevState = useRef<{ store: string | null; state: string | undefined }>({ store: null, state: undefined });
  useEffect(() => {
    const prev = prevState.current;
    if (prev.store === store && prev.state === "running" && job && job.state !== "running") {
      if (job.state === "failed") toast({ kind: "error", title: "Challenger training failed", body: job.error });
      else
        toast({
          kind: job.decision === "promoted" ? "promoted" : "rejected",
          title: job.decision === "promoted" ? "Store-specific model promoted" : "Challenger not promoted",
          body: job.reason,
        });
    }
    prevState.current = { store, state: job?.state };
  }, [job, store, toast]);

  async function retrain() {
    if (!store) return;
    setStarting(true);
    try {
      await postJSON<RetrainJob>(`/api/stores/${enc(store)}/retrain`, {});
      reload();
    } catch (e) {
      toast({ kind: "error", title: "Couldn't start training", body: (e as ApiError).message });
    } finally {
      setStarting(false);
    }
  }

  if (!L) {
    return (
      <Panel title="Learning status">
        <StateBlock loading={learning.loading || !store} error={learning.error} onRetry={learning.reload} />
      </Panel>
    );
  }

  const need = L.retrain_threshold_days + EVAL_DAYS;
  const g = L.graded_forecasts;
  const reasons = Object.entries(L.quarantine_reasons).sort((a, b) => b[1] - a[1]);

  return (
    <Panel
      title="Learning status"
      sub="One model serves every store. This store's sheets grade it, and they can retrain it — it's replaced only if it gets better here without getting worse anywhere else."
      aside={learning.loading ? <span className="spinner spinner--inline" aria-label="updating" /> : undefined}
    >
      <div className="learn">
        <div>
          <h3 className="h3">Serving model</h3>
          <div className="learn__ver">
            <code className="ver ver--xl">{L.serving_version}</code>
            <span className={`chip ${L.has_learned_from_this_store ? "chip--status-champion" : ""}`}>
              {L.has_learned_from_this_store ? "Learned from this store" : "Not yet trained on this store"}
            </span>
          </div>
          <p className="champ__reason">
            {L.has_learned_from_this_store
              ? "The one global model now includes this store's own sheets — it was promoted because it beat the previous version here, on days neither had seen, without hurting the other stores."
              : "Cold start: the model hasn't trained on this store's sheets yet, so it forecasts from what it learned elsewhere."}
          </p>
          <dl className="facts facts--tight">
            <div>
              <dt>Uploaded days</dt>
              <dd>
                <strong>{int(L.uploaded_days)}</strong>
                {L.first_day && (
                  <span className="muted">
                    {" "}
                    · {day(L.first_day)} → {day(L.last_day)}
                  </span>
                )}
              </dd>
            </div>
            <div>
              <dt>Sheets uploaded</dt>
              <dd>{int(L.uploads)}</dd>
            </div>
            <div>
              <dt>Real waste recorded</dt>
              <dd>{int(L.real_waste_units)} units, from this store's own sheets</dd>
            </div>
          </dl>
        </div>

        <div>
          <h3 className="h3">Forecast error on this store's own data</h3>
          {g && isNum(g.wape) ? (
            <>
              <div className="obs">
                <span className="obs__n">{wape(g.wape)}</span>
                <span className="obs__l">WAPE over {int(g.n)} graded forecasts</span>
              </div>
              <p className="champ__reason">
                Bias <strong>{spct(g.bias, 1)}</strong>{" "}
                {isNum(g.bias) && Math.abs(g.bias) >= 0.02 ? (g.bias > 0 ? "(forecasts run high: waste risk)" : "(forecasts run low: stockout risk)") : "(balanced)"}.
                Graded forecasts are ones made for days the model never trained on, compared with what the sheet says sold.
              </p>
            </>
          ) : (
            <p className="muted">No graded forecasts yet. They appear once a sheet covers days after the model's training data.</p>
          )}
          <h3 className="h3 learn__gap">Quarantined rows</h3>
          {L.quarantined_rows > 0 ? (
            <>
              <p className="learn__q">
                <strong>{int(L.quarantined_rows)}</strong> rows held back across all uploads — not trained on until fixed and re-uploaded.
              </p>
              <ul className="upload__reasons">
                {reasons.map(([code, n]) => (
                  <li key={code} title={reasonHint(code) ?? code}>
                    {int(n)} × {reasonText(code)}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="muted">None. Every uploaded row passed validation.</p>
          )}
        </div>

        <div>
          <h3 className="h3">Toward retraining on this store</h3>
          <div className="obs">
            <span className="obs__n">
              {int(L.new_days_since_model_training)}
              <span className="obs__of"> / {need}</span>
            </span>
            <span className="obs__l">new days since the model's training data</span>
          </div>
          <Meter value={L.new_days_since_model_training} max={need} label="New days toward retraining" />
          <p className="note">
            {L.explanation} That's {L.retrain_threshold_days} days to learn from + {EVAL_DAYS} to judge on.
          </p>
          {L.retrain_eligible ? (
            <p className="elig elig--yes">Enough new days to train a challenger.</p>
          ) : (
            <p className="elig">{Math.max(need - L.new_days_since_model_training, 0)} more days needed.</p>
          )}
          {L.retrain_eligible && (
            <div className="learn__retrain">
              <button className="btn btn--primary btn--sm" onClick={retrain} disabled={running || starting}>
                {running ? "Training…" : starting ? "Starting…" : "Retrain the model with this store's sheets"}
              </button>
              <p className="note">
                It replaces the model for every store only if it wins here on the most recent {EVAL_DAYS} days neither version has seen, and doesn't get worse for the other stores. Otherwise
                it's recorded and rejected.
              </p>
            </div>
          )}
          {job && <RetrainOutcome job={job} />}
        </div>
      </div>
    </Panel>
  );
}

function RetrainOutcome({ job }: { job: RetrainJob }) {
  if (job.state === "running")
    return (
      <p className="learn__job" role="status">
        <span className="spinner spinner--inline" aria-hidden /> Training a challenger (started {stamp(job.started_at)}). Checking
        every 5 s…
      </p>
    );
  if (job.state === "failed")
    return (
      <p className="learn__job upload__err" role="alert">
        Last training run failed: {job.error}
      </p>
    );
  return (
    <div className="learn__job" role="status">
      <div>
        <DecisionChip decision={job.decision} /> {job.candidate && <code className="ver">{job.candidate}</code>}
      </div>
      {(isNum(job.champion_wape) || isNum(job.challenger_wape)) && (
        <p className="learn__vs">
          Serving model <strong>{wape(job.champion_wape)}</strong> vs challenger <strong>{wape(job.challenger_wape)}</strong> WAPE on
          the held-out days
        </p>
      )}
      <p className="champ__reason">{job.reason}</p>
      <p className="note">
        {job.decision === "promoted"
          ? `Now serving ${job.candidate ?? "the challenger"}; tomorrow's plan was rebuilt with it.`
          : `Still serving ${job.champion ?? "the current model"}. A challenger is promoted only if it wins.`}
      </p>
    </div>
  );
}

// ---------- Adherence & discount response ----------

function AdherencePanel({ L, learning }: { L: LearningStatus | undefined; learning: LearningApi }) {
  const a = L?.adherence;
  return (
    <Panel title="Plan adherence" sub="Did the store order what the plan recommended, and what happened when it didn't?">
      {!a ? (
        <StateBlock compact loading={learning.loading} error={learning.error} onRetry={learning.reload} />
      ) : a.compared_orders === 0 ? (
        <StateBlock compact empty emptyText="Adherence appears once a sheet covers days we planned." />
      ) : (
        <>
          <div className="kpis kpis--flat">
            <div className="kpi">
              <span className="kpi__v">{pct(a.followed_share, 0)}</span>
              <span className="kpi__l">orders within ±10% of the plan</span>
            </div>
            <div className="kpi">
              <span className="kpi__v">{int(a.over_ordered)}</span>
              <span className="kpi__l">over-ordered</span>
            </div>
            <div className="kpi">
              <span className="kpi__v">{int(a.under_ordered)}</span>
              <span className="kpi__l">under-ordered</span>
            </div>
            <div className="kpi">
              <span className="kpi__v">{int(a.excess_units)}</span>
              <span className="kpi__l">units ordered above plan</span>
            </div>
            <div className="kpi">
              <span className="kpi__v kpi__v--risk">{int(a.waste_after_over_ordering)}</span>
              <span className="kpi__l">waste that followed over-ordering</span>
            </div>
          </div>
          <p className="note">
            Of {int(a.compared_orders)} planned orders compared with what was received. Waste is attributed to over-ordering only within
            the product's shelf life after the order, and never more than the excess.
          </p>
          {a.worst && a.worst.length > 0 && (
            <div className="subsection">
              <h3 className="h3">Largest over-orders</h3>
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Date</th>
                    <th className="num">Recommended</th>
                    <th className="num">Received</th>
                    <th className="num">Over</th>
                  </tr>
                </thead>
                <tbody>
                  {a.worst.map((w) => (
                    <tr key={`${w.product_id}-${w.forecast_date}`}>
                      <td>
                        <span className="item__name">{w.product_name ?? `Product ${w.product_id}`}</span>
                        <span className="item__id">#{w.product_id}</span>
                      </td>
                      <td className="nowrap">{day(w.forecast_date, { year: false })}</td>
                      <td className="num">{num(w.recommended_order, 0)}</td>
                      <td className="num">{num(w.units_received, 0)}</td>
                      <td className="num err--over">{spct(w.pct, 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Panel>
  );
}

function DiscountPanel({ L, learning }: { L: LearningStatus | undefined; learning: LearningApi }) {
  const m = L?.measured_discount_response;
  const cats = m ? Object.keys(m).sort() : [];
  const levels = m
    ? [...new Set(cats.flatMap((c) => Object.keys(m[c])))].sort((a, b) => parseFloat(a) - parseFloat(b))
    : [];
  return (
    <Panel
      title="Discount response"
      sub={L ? `Measured from ${int(L.promotion_experiments)} promotion days: extra sales at each markdown, vs. the forecast without it.` : undefined}
    >
      {!m ? (
        <StateBlock compact loading={learning.loading} error={learning.error} onRetry={learning.reload} />
      ) : !cats.length ? (
        <StateBlock
          compact
          empty
          emptyText="Not enough promotion days yet — markdowns use the model's estimate from historical discounts."
        />
      ) : (
        <>
          <table className="table table--compact">
            <thead>
              <tr>
                <th>Category</th>
                {levels.map((lv) => (
                  <th key={lv} className="num">
                    {lv} off
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cats.map((c) => (
                <tr key={c}>
                  <td>{c}</td>
                  {levels.map((lv) => (
                    <td key={lv} className="num">
                      {isNum(m[c][lv]) ? spct(m[c][lv], 0) : <span className="muted">model est.</span>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note">
            Measured levels replace the model's estimate in this store's markdown plans; levels without enough promotion days keep the
            model estimate.
          </p>
        </>
      )}
    </Panel>
  );
}

// ---------- Sell-through ----------

function SellThroughPanel({ store, L, learning }: { store: string | null; L: LearningStatus | undefined; learning: LearningApi }) {
  const rows: SellThroughRow[] = L?.sell_through ?? [];
  return (
    <Panel
      flush
      title="Sell-through before spoilage"
      sub="Per product, from this store's sheets: of what went on the shelf, how much sold before it spoiled. Most waste first (top 50; the CSV has all)."
      aside={
        store && rows.length > 0 ? (
          <a className="btn btn--sm" href={`/api/stores/${enc(store)}/sell-through.csv`} download>
            Download CSV
          </a>
        ) : undefined
      }
    >
      {!L ? (
        <StateBlock loading={learning.loading} error={learning.error} onRetry={learning.reload} />
      ) : !rows.length ? (
        <StateBlock empty emptyText="No sheets uploaded for this store yet." />
      ) : (
        <div className="tablewrap tablewrap--tall">
          <table className="table">
            <thead>
              <tr>
                <th>Product</th>
                <th>Category</th>
                <th className="num">Days</th>
                <th className="num">Received</th>
                <th className="num">Sold</th>
                <th className="num">Wasted</th>
                <th className="num">Sold before spoilage</th>
                <th className="num">Spoilage rate</th>
                <th className="num">Shelf life</th>
                <th>Source</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const exact = (r.source ?? "").startsWith("batch");
                return (
                  <tr key={r.product_id}>
                    <td>
                      <span className="item__name">{r.product_name ?? `Product ${r.product_id}`}</span>
                      <span className="item__id">#{r.product_id}</span>
                    </td>
                    <td className="nowrap muted">{r.category ?? "–"}</td>
                    <td className="num">{int(r.days)}</td>
                    <td className="num">{int(r.received)}</td>
                    <td className="num">{int(r.sold)}</td>
                    <td className="num">{int(r.wasted)}</td>
                    <td className="num">{pct(r.sell_through_before_spoilage, 0)}</td>
                    <td className={`num ${isNum(r.spoilage_rate) && r.spoilage_rate >= 0.15 ? "err--over" : ""}`}>{pct(r.spoilage_rate, 1)}</td>
                    <td className="num">{isNum(r.shelf_life_days) ? `${num(r.shelf_life_days, 0)} d` : "–"}</td>
                    <td>
                      <span className={`chip ${exact ? "chip--status-champion" : ""}`} title={r.source ?? undefined}>
                        {exact ? "Batch: exact" : "Daily-sheet estimate"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {L && rows.length > 0 && (
        <p className="note note--pad">
          Sold before spoilage = sold ÷ (sold + wasted); spoilage rate = wasted ÷ received. Daily sheets are estimated first-in,
          first-out; a batch sheet makes them exact per delivery.
        </p>
      )}
    </Panel>
  );
}

// ---------- Upload history ----------

function UploadHistory({ store, uploads }: { store: string | null; uploads: ReturnType<typeof useApi<UploadRecord[]>> }) {
  const list = (uploads.data ?? []).filter((u) => u.store_id === store);
  return (
    <Panel flush title="Upload history" sub="Every sheet this store has uploaded. Uploads are append-only; a re-sent day is a duplicate, not an edit.">
      {!uploads.data ? (
        <StateBlock loading={uploads.loading || !store} error={uploads.error} onRetry={uploads.reload} />
      ) : !list.length ? (
        <StateBlock empty emptyText="Nothing uploaded for this store yet." />
      ) : (
        <div className="tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th>Uploaded</th>
                <th>Kind</th>
                <th>File</th>
                <th>Covers</th>
                <th className="num">Accepted</th>
                <th className="num">Quarantined</th>
                <th className="num">Days graded</th>
                <th className="num">Forecasts graded</th>
              </tr>
            </thead>
            <tbody>
              {list.map((u) => {
                const batch = u.kind === "batch";
                return (
                  <tr key={u.upload_id}>
                    <td className="nowrap muted">{stamp(u.created_at)}</td>
                    <td>
                      <span className="chip">{batch ? "Batch" : "Daily"}</span>
                    </td>
                    <td className="wrap">{u.filename ?? "–"}</td>
                    <td className="nowrap">{u.first_date ? `${shortDay(u.first_date)} → ${day(u.last_date, { dow: false })}` : "–"}</td>
                    <td className="num">{int(u.accepted)}</td>
                    <td className="num">
                      {(u.quarantined ?? 0) > 0 && store ? (
                        <>
                          <span className="err--over">{int(u.quarantined)}</span>{" "}
                          <a className="link" href={`/api/stores/${enc(store)}/uploads/${enc(u.upload_id)}/quarantine.csv`} download>
                            rejected rows CSV
                          </a>
                        </>
                      ) : (
                        <span className="muted">0</span>
                      )}
                    </td>
                    <td className="num">{batch ? <span className="muted">–</span> : int(u.evaluated_days)}</td>
                    <td className="num">{batch ? <span className="muted">–</span> : int(u.outcomes_attached)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
