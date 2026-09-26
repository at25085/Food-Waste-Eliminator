import { useEffect, useMemo, useState } from "react";
import { qs, useApi } from "../api";
import type { LedgerRow, Store } from "../types";
import { Panel, Segmented, StateBlock, WeatherGlyph } from "../components/ui";
import { day, isNum, num, signed, stamp, wape, weatherText } from "../format";

type Ctx = "replay" | "production" | "store_eval";

const CTX_TITLE: Record<Ctx, string> = {
  replay: "Backtest predictions (historical replay, not live customers)",
  production: "Production predictions",
  store_eval: "Graded uploads: what the serving model would have predicted for each uploaded day it never trained on",
};

export default function Ledger({ stores }: { stores: Store[] }) {
  const [ctx, setCtx] = useState<Ctx>("replay");
  const [store, setStore] = useState("");
  const [productInput, setProductInput] = useState("");
  const [product, setProduct] = useState("");
  const [limit, setLimit] = useState(200);

  useEffect(() => {
    const t = window.setTimeout(() => setProduct(productInput.trim()), 350);
    return () => window.clearTimeout(t);
  }, [productInput]);

  const q = useApi<LedgerRow[]>(`/api/ledger${qs({ context: ctx, store: store || undefined, product: product || undefined, limit })}`);
  const rows = q.data ?? [];

  const summary = useMemo(() => {
    const done = rows.filter((r) => isNum(r.actual_units_sold));
    const absErr = done.reduce((s, r) => s + Math.abs(r.error ?? 0), 0);
    const act = done.reduce((s, r) => s + (r.actual_units_sold ?? 0), 0);
    return { n: rows.length, withOutcome: done.length, wape: act > 0 ? absErr / act : null };
  }, [rows]);

  return (
    <div className="screen">
      <header className="screen__head">
        <div>
          <h1 className="display">Prediction ledger</h1>
          <p className="screen__lede">Every forecast the system made, next to what actually sold.</p>
        </div>
        <Segmented<Ctx>
          label="Ledger context"
          value={ctx}
          onChange={setCtx}
          options={[
            { value: "replay", label: "Backtest replay" },
            { value: "production", label: "Production" },
            { value: "store_eval", label: "Graded uploads (retroactive)", title: "Retroactive forecasts for owner-uploaded days, graded against what the sheet says sold" },
          ]}
        />
      </header>

      <div className="appendonly">
        <svg viewBox="0 0 24 24" aria-hidden>
          <path d="M6 3h9l3 3v15H6z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
          <path d="M9 10h6M9 14h6M9 18h3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
        </svg>
        <p>
          <strong>Predictions are append-only.</strong> Outcomes are attached later, in a separate table, and never overwrite what the
          model believed at the time.
        </p>
      </div>

      <Panel
        flush
        title={CTX_TITLE[ctx]}
        sub={
          q.data
            ? `${summary.n} most recent rows${summary.withOutcome ? `, ${summary.withOutcome} with outcomes (WAPE ${wape(summary.wape)})` : ""}${
                summary.n - summary.withOutcome > 0 ? `, ${summary.n - summary.withOutcome} awaiting outcome` : ""
              }.`
            : undefined
        }
        aside={
          <div className="filters">
            <select value={store} onChange={(e) => setStore(e.target.value)} aria-label="Store">
              <option value="">All stores</option>
              {stores.map((s) => (
                <option key={s.store_id} value={s.store_id}>
                  {s.store_id.replace("_", " ")}
                  {s.kind === "demo" || s.kind === "sample" ? ` (${s.kind})` : ""}
                </option>
              ))}
            </select>
            <input type="search" placeholder="Product id" value={productInput} onChange={(e) => setProductInput(e.target.value)} aria-label="Product id" inputMode="numeric" />
            <select value={limit} onChange={(e) => setLimit(parseInt(e.target.value, 10))} aria-label="Rows">
              <option value={100}>100 rows</option>
              <option value={200}>200 rows</option>
              <option value={500}>500 rows</option>
              <option value={1000}>1,000 rows</option>
            </select>
            <a
              className="btn btn--sm"
              href={`/api/ledger.csv${qs({ context: ctx, store: store || undefined })}`}
              download
              title="Every row for this context and store (not just the rows shown)"
            >
              Download CSV
            </a>
          </div>
        }
      >
        {!q.data ? (
          <StateBlock loading={q.loading} error={q.error} onRetry={q.reload} />
        ) : !rows.length ? (
          <StateBlock
            empty
            emptyText={
              ctx === "store_eval" ? (
                <>
                  <strong>No graded uploads{store ? ` for ${store}` : ""}.</strong> They're written when a store uploads a daily sheet
                  covering days after the model's training data (Store data screen).
                </>
              ) : ctx === "production" ? (
                <>
                  <strong>No production predictions yet.</strong> They're written when the production pipeline builds tomorrow's plan.
                </>
              ) : product ? (
                `No predictions for product ${product}${store ? ` in ${store}` : ""}. Product ids are numeric, e.g. 403.`
              ) : (
                "No replay predictions yet. The backtest may still be starting."
              )
            }
          />
        ) : (
          <div className="tablewrap tablewrap--tall">
            <table className="table table--ledger">
              <thead>
                <tr>
                  <th>Forecast for</th>
                  <th>Store</th>
                  <th>Product</th>
                  <th className="num">Predicted</th>
                  <th className="num">P80</th>
                  <th className="num">Actual</th>
                  <th className="num">Error</th>
                  <th>Model</th>
                  <th>Predicted at</th>
                  <th>Weather forecast used</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const w = r.weather_forecast;
                  const has = isNum(r.actual_units_sold);
                  return (
                    <tr key={r.prediction_id}>
                      <td className="nowrap">{day(r.forecast_date)}</td>
                      <td className="nowrap">{r.store_id.replace("_", " ")}</td>
                      <td>
                        <button className="link" onClick={() => setProductInput(r.product_id)} title="Show only this product">
                          #{r.product_id}
                        </button>
                        <span className="item__id"> {r.category}</span>
                      </td>
                      <td className="num">{num(r.predicted_units, 1)}</td>
                      <td className="num muted">{num(r.p80_units, 1)}</td>
                      <td className="num">{has ? num(r.actual_units_sold, 1) : <span className="pending">awaiting outcome</span>}</td>
                      <td className={`num err ${has ? ((r.error ?? 0) > 0 ? "err--over" : "err--under") : ""}`}>{has ? signed(r.error, 1) : "–"}</td>
                      <td>
                        <code className="ver">{r.model_version}</code>
                      </td>
                      <td className="nowrap muted">{stamp(r.prediction_created_at)}</td>
                      <td className="wxcell">
                        {w ? (
                          <>
                            <WeatherGlyph code={w.weather_code} size={16} />
                            <span>
                              {weatherText(w.weather_code)}, {num(w.weather_temperature_max, 0)}°/{num(w.weather_temperature_min, 0)}°,{" "}
                              {num(w.weather_precipitation_sum, 1)} mm
                            </span>
                            {w.weather_source && <span className="item__id"> {w.weather_source === "previous_runs_d1" ? "issued day before" : w.weather_source.replace(/_/g, " ")}</span>}
                          </>
                        ) : (
                          "–"
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="note note--pad">Error = predicted − actual. Positive means over-forecast (waste risk); negative means under-forecast (stockout risk).</p>
      </Panel>
    </div>
  );
}
