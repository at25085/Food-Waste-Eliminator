import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { qs, useApi } from "../api";
import type { RecItem, Recommendations, Store, Timeseries } from "../types";
import { Panel, RiskBadge, StateBlock, Sticker } from "../components/ui";
import StorePicker from "../components/StorePicker";
import { C, CATEGORIES, axisProps, categoryColor, categoryLabel } from "../chartTheme";
import { day, int, shortDay } from "../format";

const ORDER_LIST_LEN = 10;
const HISTORY_DAYS = 14;

interface HistoryRow {
  date: string;
  actual: number | null;
  predicted: number | null;
  tomorrow: number | null;
}

/** The landing dashboard (the team's original layout), every number from the API. */
export default function Overview(props: { stores: Store[]; store: string | null; setStore: (s: string) => void }) {
  const { store, stores } = props;
  const recs = useApi<Recommendations>(store ? `/api/stores/${encodeURIComponent(store)}/recommendations` : null);
  const ts = useApi<Timeseries>(store ? `/api/metrics/timeseries${qs({ context: "production", store })}` : null);

  const data = recs.data && recs.data.store_id === store ? recs.data : undefined;
  const items = data?.items ?? [];

  const totals = useMemo(() => {
    const sum = (f: (i: RecItem) => number) => items.reduce((a, i) => a + (f(i) || 0), 0);
    return {
      demand: sum((i) => i.p50),
      order: sum((i) => i.order_qty),
      onHand: sum((i) => i.on_hand),
      surplus: sum((i) => i.surplus_units ?? 0),
      atRisk: items.filter((i) => i.waste_risk !== "low").length,
    };
  }, [items]);

  const byCategory = useMemo(
    () =>
      CATEGORIES.map((c) => ({
        category: c.label,
        units: Math.round(items.filter((i) => i.category === c.key).reduce((a, i) => a + i.p50, 0)),
        color: c.color,
      })).filter((c) => c.units > 0),
    [items],
  );

  const history = useMemo(() => {
    const days: HistoryRow[] = (ts.data?.days ?? []).slice(-HISTORY_DAYS).map((d) => ({
      date: d.forecast_date,
      actual: Math.round(d.actual),
      predicted: Math.round(d.actual + d.err),
      tomorrow: null,
    }));
    // Tomorrow's forecast joins the line when it directly follows the graded days.
    const last = days[days.length - 1];
    if (data?.forecast_date && items.length && (!last || data.forecast_date > last.date)) {
      if (last) last.tomorrow = last.predicted;
      days.push({ date: data.forecast_date, actual: null, predicted: null, tomorrow: Math.round(totals.demand) });
    }
    return days;
  }, [ts.data, data?.forecast_date, items.length, totals.demand]);

  const toOrder = useMemo(() => [...items].sort((a, b) => b.order_qty - a.order_qty).slice(0, ORDER_LIST_LEN), [items]);

  return (
    <div className="screen screen--tight">
      <header className="overview__head panel">
        <div>
          <h1 className="display">Dashboard</h1>
          {data?.forecast_date && (
            <p className="screen__lede">
              Plan for <strong>{day(data.forecast_date, { dow: true, year: true })}</strong>
            </p>
          )}
        </div>
        <StorePicker stores={stores} value={store} onChange={props.setStore} />
      </header>

      {!data ? (
        <Panel>
          <StateBlock
            loading={recs.loading || !store}
            error={recs.error}
            onRetry={recs.reload}
            notFound={
              <p>
                <strong>No plan for this store yet.</strong> Upload a daily sheet on the <a href="#/data">Upload</a> screen.
              </p>
            }
          />
        </Panel>
      ) : (
        <>
          <div className="ov-kpis">
            <Kpi tone="blue" label="Customers per day" value={data.recent_customers_7d == null ? "—" : int(data.recent_customers_7d)} sub="7-day average" />
            <Kpi tone="green" label="Expected sales tomorrow" value={`${int(totals.demand)} units`} sub={`${items.length} products`} />
            <Kpi tone="orange" label="To order tomorrow" value={`${int(totals.order)} units`} sub={`${int(totals.onHand)} already on the shelf`} />
            <Kpi tone="red" label="At risk of waste" value={`${int(totals.surplus)} units`} sub={`${totals.atRisk} products to discount or donate`} />
          </div>

          <div className="overview__grid">
            <div className="overview__charts">
              <Panel title="Expected sales by category">
                <div className="chartbox">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={byCategory} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
                      <CartesianGrid vertical={false} stroke={C.grid} />
                      <XAxis dataKey="category" {...axisProps} interval={0} />
                      <YAxis {...axisProps} allowDecimals={false} />
                      <Tooltip formatter={(v) => [`${int(Number(v))} units`, "Expected sales"]} cursor={{ fill: C.bandA }} />
                      <Bar dataKey="units" radius={[4, 4, 0, 0]}>
                        {byCategory.map((c) => (
                          <Cell key={c.category} fill={c.color} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </Panel>

              <Panel title="Forecast vs. what sold">
                <div className="chartbox">
                  {history.length > 1 ? (
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={history} margin={{ top: 8, right: 12, left: -8, bottom: 0 }}>
                        <CartesianGrid vertical={false} stroke={C.grid} />
                        <XAxis dataKey="date" {...axisProps} tickFormatter={shortDay} minTickGap={16} />
                        <YAxis {...axisProps} allowDecimals={false} domain={["auto", "auto"]} />
                        <Tooltip labelFormatter={(d) => day(String(d), { dow: true })} formatter={(v, n) => [`${int(Number(v))} units`, String(n)]} />
                        <Legend iconType="plainline" wrapperStyle={{ fontSize: 12 }} />
                        <Line dataKey="actual" name="Sold" stroke={C.ink} strokeWidth={2} dot={false} connectNulls={false} />
                        <Line dataKey="predicted" name="Forecast" stroke={C.forecast} strokeWidth={2.5} dot={false} connectNulls={false} />
                        <Line dataKey="tomorrow" name="Tomorrow" stroke={C.forecast} strokeWidth={2.5} strokeDasharray="5 4" dot={{ r: 3 }} connectNulls />
                      </LineChart>
                    </ResponsiveContainer>
                  ) : (
                    <StateBlock loading={ts.loading} error={ts.error} empty emptyText="Upload two days of sheets to compare forecasts with sales." />
                  )}
                </div>
              </Panel>
            </div>

            <div className="orderpanel">
              <Panel title="What to order">
                <ul className="orderlist">
                  {toOrder.map((i) => (
                    <li key={i.product_id} className="orderlist__row">
                      <span className="orderlist__dot" style={{ background: categoryColor(i.category) }} title={categoryLabel(i.category)} aria-hidden />
                      <span className="orderlist__name">
                        {i.name ?? i.product_id}
                        {i.waste_risk !== "low" && <RiskBadge risk={i.waste_risk} />}
                        {i.markdown > 0 && <Sticker depth={i.markdown} />}
                      </span>
                      <span className="orderlist__qty">{int(i.order_qty)} units</span>
                    </li>
                  ))}
                </ul>
                <a className="orderpanel__all" href="#/today">
                  See all {items.length} products
                </a>
                <a className="btn btn--primary orderpanel__btn" href={`/api/stores/${encodeURIComponent(store ?? "")}/plan.csv`} download>
                  Download order report
                </a>
              </Panel>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Kpi(props: { label: string; value: string; sub: string; tone: "blue" | "green" | "orange" | "red" }) {
  return (
    <div className={`ov-kpi ov-kpi--${props.tone}`}>
      <h3 className="ov-kpi__label">{props.label}</h3>
      <p className="ov-kpi__value">{props.value}</p>
      <p className="ov-kpi__sub">{props.sub}</p>
    </div>
  );
}
