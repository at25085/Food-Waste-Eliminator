import { useEffect, useMemo, useState } from "react";
import { qs, useApi } from "../api";
import type { LedgerRow, Recommendations, Store } from "../types";
import { Panel, StateBlock } from "../components/ui";
import { categoryLabel } from "../chartTheme";
import { day, isNum, num, wape } from "../format";

/** Every forecast, saved before the sales happened, next to what actually sold. */
/** How far the forecast was from what sold, as a share of what sold (e.g. "+8%" = forecast 8% high). */
function pctDiff(pred: number, actual: number): string {
  if (actual <= 0) return pred > 0.5 ? "over" : "0%";
  const d = Math.round(((pred - actual) / actual) * 100);
  return `${d > 0 ? "+" : ""}${d}%`;
}

export default function Ledger({ stores, store: selected }: { stores: Store[]; store: string | null }) {
  const [store, setStore] = useState(selected ?? "");
  const [productInput, setProductInput] = useState("");
  const [product, setProduct] = useState("");
  const [limit, setLimit] = useState(200);

  useEffect(() => setStore(selected ?? ""), [selected]);
  useEffect(() => {
    const t = window.setTimeout(() => setProduct(productInput.trim()), 350);
    return () => window.clearTimeout(t);
  }, [productInput]);

  const shown = store || stores.map((s) => s.store_id).join(",");
  const q = useApi<LedgerRow[]>(store ? `/api/ledger${qs({ context: "production", store, product: product || undefined, limit, graded: "true" })}` : null);
  const pending = useApi<{ count: number; forecast_date: string | null }>(store ? `/api/ledger/pending${qs({ context: "production", store })}` : null);
  const recs = useApi<Recommendations>(store ? `/api/stores/${encodeURIComponent(store)}/recommendations` : null);
  const names = useMemo(() => new Map((recs.data?.items ?? []).map((i) => [i.product_id, i.name ?? i.product_id])), [recs.data]);
  const rows = q.data ?? [];

  const summary = useMemo(() => {
    const done = rows.filter((r) => isNum(r.actual_units_sold));
    const absErr = done.reduce((s, r) => s + Math.abs(r.predicted_units - (r.actual_units_sold ?? 0)), 0);
    const act = done.reduce((s, r) => s + (r.actual_units_sold ?? 0), 0);
    return { n: rows.length, withOutcome: done.length, wape: act > 0 ? absErr / act : null };
  }, [rows]);

  return (
    <div className="screen">
      <header className="screen__head">
        <div>
          <h1 className="display">Ledger</h1>
          <p className="screen__lede">Every forecast, saved before the day's sales, next to what actually sold.</p>
        </div>
      </header>

      <Panel
        flush
        title={stores.find((s) => s.store_id === store)?.city ?? "Forecasts"}
        sub={
          q.data
            ? `${summary.n} most recent forecasts with their sales, off by ${wape(summary.wape)} overall${
                pending.data?.count ? ` · ${pending.data.count} forecasts for ${day(pending.data.forecast_date)} are waiting for that day's sales` : ""
              }`
            : undefined
        }
        aside={
          <div className="filters">
            <select value={store} onChange={(e) => setStore(e.target.value)} aria-label="Store">
              {stores.map((s) => (
                <option key={s.store_id} value={s.store_id}>
                  {s.city}
                </option>
              ))}
            </select>
            <input type="search" placeholder="Product" value={productInput} onChange={(e) => setProductInput(e.target.value)} aria-label="Product id" />
            <select value={limit} onChange={(e) => setLimit(parseInt(e.target.value, 10))} aria-label="Rows">
              <option value={100}>100 rows</option>
              <option value={200}>200 rows</option>
              <option value={500}>500 rows</option>
              <option value={1000}>1,000 rows</option>
            </select>
            <a className="btn btn--sm" href={`/api/ledger.csv${qs({ context: "production", store: store || undefined })}`} download>
              Download CSV
            </a>
          </div>
        }
      >
        {!shown ? (
          <StateBlock empty emptyText="Add a store on the Upload screen first." />
        ) : !q.data ? (
          <StateBlock loading={q.loading} error={q.error} onRetry={q.reload} />
        ) : !rows.length ? (
          <StateBlock empty emptyText={product ? `No forecasts for product ${product}.` : "No forecasts yet. They appear after the first daily sheet is uploaded."} />
        ) : (
          <div className="tablewrap tablewrap--tall">
            <table className="table table--ledger">
              <thead>
                <tr>
                  <th>Store</th>
                  <th>Product</th>
                  <th className="num">Predicted</th>
                  <th className="num">Actual</th>
                  <th className="num">Difference</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const has = isNum(r.actual_units_sold);
                  return (
                    <tr key={r.prediction_id}>
                      <td className="nowrap">{stores.find((s) => s.store_id === r.store_id)?.city ?? r.store_id}</td>
                      <td>
                        <button className="link" onClick={() => setProductInput(r.product_id)} title="Show only this product">
                          {names.get(r.product_id) ?? `#${r.product_id}`}
                        </button>
                        {r.category && <span className="item__id"> {categoryLabel(r.category)}</span>}
                      </td>
                      <td className="num">{num(r.predicted_units, 0)}</td>
                      <td className="num">{has ? num(r.actual_units_sold, 0) : <span className="pending">not in yet</span>}</td>
                      <td className={`num ${has ? (r.predicted_units > (r.actual_units_sold ?? 0) ? "err--over" : "err--under") : ""}`}>
                        {has ? pctDiff(r.predicted_units, r.actual_units_sold ?? 0) : "–"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}
