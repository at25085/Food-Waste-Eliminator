import { useEffect, useMemo, useRef, useState } from "react";
import { qs, useApi } from "../api";
import type { LiveWeather, MarkdownStep, PromotionExperiment, RecItem, Recommendations, Store, WasteRisk } from "../types";
import { Pager, Panel, RiskBadge, StateBlock, Sticker, WeatherGlyph } from "../components/ui";
import StorePicker from "../components/StorePicker";
import { categoryLabel } from "../chartTheme";
import { day, fahrenheit, inches, int, isNum, mph, num, shortDay, spct, weatherText } from "../format";

const RISK_ORDER: Record<WasteRisk, number> = { high: 0, watch: 1, low: 2 };
type SortKey = "risk" | "name" | "category" | "p50" | "p80" | "order_qty" | "on_hand" | "expiring_tomorrow" | "markdown" | "plan";

const ITEMS_PER_PAGE = 25;
const MARGIN_RATIO = 0.3; // mirrors decisions.policy.critical_ratio default (cu)

export default function Today(props: { stores: Store[]; store: string | null; setStore: (s: string) => void }) {
  const { store } = props;
  // Starts at the backend's default operating point (~5% lost sales); the manager can move it.
  const [wcr, setWcr] = useState(0.1);
  const [wcrQuery, setWcrQuery] = useState(0.1);
  const [category, setCategory] = useState<string>("all");
  const [risk, setRisk] = useState<"all" | WasteRisk | "markdown">("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "risk", dir: 1 });
  const [page, setPage] = useState(0);

  // Debounce the slider so dragging doesn't fire a request per pixel.
  useEffect(() => {
    const t = window.setTimeout(() => setWcrQuery(wcr), 180);
    return () => window.clearTimeout(t);
  }, [wcr]);

  const recs = useApi<Recommendations>(store ? `/api/stores/${store}/recommendations${qs({ waste_cost_ratio: wcrQuery.toFixed(2) })}` : null);
  const live = useApi<LiveWeather>(store ? `/api/stores/${store}/weather/live` : null);
  const promos = useApi<PromotionExperiment[]>(store ? `/api/promotions${qs({ store })}` : null);

  const data = recs.data && recs.data.store_id === store ? recs.data : undefined;

  // Remember the previous order quantities (same store) so a slider move shows what changed.
  const lastRef = useRef<{ store: string; wcr: number; orders: Map<string, number> } | null>(null);
  const [deltas, setDeltas] = useState<{ map: Map<string, number>; total: number; from: number } | null>(null);
  useEffect(() => {
    if (!data) return;
    const orders = new Map(data.items.map((i) => [i.product_id, i.order_qty]));
    const prev = lastRef.current;
    if (prev && prev.store === data.store_id && prev.wcr !== data.waste_cost_ratio) {
      const map = new Map<string, number>();
      let total = 0;
      for (const [k, v] of orders) {
        const d = v - (prev.orders.get(k) ?? v);
        if (Math.abs(d) >= 0.5) map.set(k, d);
        total += d;
      }
      setDeltas({ map, total, from: prev.wcr });
    } else if (!prev || prev.store !== data.store_id) {
      setDeltas(null);
    }
    lastRef.current = { store: data.store_id, wcr: data.waste_cost_ratio, orders };
  }, [data]);

  const categories = useMemo(() => {
    const m = new Map<string, number>();
    for (const i of data?.items ?? []) m.set(i.category, (m.get(i.category) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [data]);

  const rows = useMemo(() => {
    let items = data?.items ?? [];
    if (category !== "all") items = items.filter((i) => i.category === category);
    if (risk === "markdown") items = items.filter(hasMarkdown);
    else if (risk !== "all") items = items.filter((i) => i.waste_risk === risk);
    const q = search.trim().toLowerCase();
    if (q) items = items.filter((i) => (i.name ?? "").toLowerCase().includes(q) || i.product_id.includes(q));
    const val = (i: RecItem): number | string => {
      switch (sort.key) {
        case "risk":
          return RISK_ORDER[i.waste_risk] * 1e6 - i.expiring_leftover;
        case "name":
          return (i.name ?? i.product_id).toLowerCase();
        case "category":
          return i.category;
        case "plan":
          return i.surplus_units ?? 0;
        default:
          return i[sort.key] as number;
      }
    };
    return [...items].sort((a, b) => {
      const x = val(a), y = val(b);
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
    });
  }, [data, category, risk, search, sort]);
  useEffect(() => setPage(0), [store, category, risk, search, sort]);

  const totals = useMemo(() => {
    const items = data?.items ?? [];
    return {
      order: items.reduce((s, i) => s + i.order_qty, 0),
      p50: items.reduce((s, i) => s + i.p50, 0),
      high: items.filter((i) => i.waste_risk === "high").length,
      watch: items.filter((i) => i.waste_risk === "watch").length,
      markdowns: items.filter(hasMarkdown).length,
      expiring: items.reduce((s, i) => s + i.expiring_tomorrow, 0),
    };
  }, [data]);

  const ratio = MARGIN_RATIO / (MARGIN_RATIO + wcr);
  const storeInfo = props.stores.find((s) => s.store_id === store);

  const header = (key: SortKey, label: string, cls = "num") => (
    <th className={cls} aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button className="th-sort" onClick={() => setSort((s) => ({ key, dir: s.key === key ? ((-s.dir) as 1 | -1) : key === "name" || key === "category" || key === "risk" ? 1 : -1 }))}>
        {label}
        <span className="th-sort__arrow" aria-hidden>
          {sort.key === key ? (sort.dir === 1 ? "▲" : "▼") : ""}
        </span>
      </button>
    </th>
  );

  return (
    <div className="screen">
      <header className="screen__head">
        <div>
          <h1 className="display">Tomorrow's order plan</h1>
          <p className="screen__lede">
            {storeInfo ? `${storeInfo.store_id.replace("_", " ")}, ${storeInfo.city}` : "Pick a store"}
            {data?.forecast_date && (
              <>
                {" "}
                — orders for <strong>{day(data.forecast_date)}</strong>, planned at close of {day(data.as_of, { year: false })}
              </>
            )}
          </p>
        </div>
        <StorePicker stores={props.stores} value={store} onChange={props.setStore} />
      </header>

      <p className="honesty">
        <span className="honesty__tag">Read this first</span>
        {data?.real_stock ? (
          <>Stock from this store's uploaded sheets; forecasts are real model output.</>
        ) : (
          <>Inventory &amp; waste are simulated; forecasts are real model output.</>
        )}
        {data?.inventory_basis && <> {data.inventory_basis}</>}
      </p>

      {data?.donations && data.donations.units > 0 && (
        <p className="honesty">
          <span className="honesty__tag">Donate</span>
          {int(data.donations.units)} last-day units won't clear even after markdown — route them to a food-recovery
          partner: about {int(data.donations.meals)} meals' worth, {num(data.donations.co2e_kg, 0)} kg CO₂e kept out of
          landfill (assumed unit weights).
        </p>
      )}

      {store && <Briefing store={store} />}

      <div className="grid-2">
        <Panel title="What this plan is based on" sub="Model inputs for the forecast date">
          {recs.error ? (
            <StateBlock
              compact
              error={recs.error}
              onRetry={recs.reload}
              notFound={<p>No plan yet for this store. The production pipeline hasn't written recommendations.</p>}
            />
          ) : !data ? (
            <StateBlock compact loading />
          ) : (
            <dl className="facts">
              <div>
                <dt>Forecast date</dt>
                <dd>{day(data.forecast_date)}</dd>
              </div>
              <div>
                <dt>Sales known through</dt>
                <dd>{day(data.as_of)}</dd>
              </div>
              <div>
                <dt>Model version</dt>
                <dd>
                  <code className="ver">{data.model_version ?? "–"}</code>
                </dd>
              </div>
              <div>
                <dt>Customers per day</dt>
                <dd>
                  {int(data.recent_customers_7d)}
                  <span className="facts__note">7-day average (orders/day)</span>
                </dd>
              </div>
              <div className="facts__wide">
                <dt>Weather the model used</dt>
                <dd className="wxline">
                  <WeatherGlyph code={data.weather.weather_code} />
                  <span>
                    <strong>{weatherText(data.weather.weather_code)}</strong>, {fahrenheit(data.weather.weather_temperature_max)} /{" "}
                    {fahrenheit(data.weather.weather_temperature_min)}, {inches(data.weather.weather_precipitation_sum)} rain
                    {isNum(data.weather.weather_wind_speed_max) && <>, wind {mph(data.weather.weather_wind_speed_max)}</>}
                  </span>
                </dd>
              </div>
            </dl>
          )}
        </Panel>

        <Panel
          title={live.data ? `${live.data.city} weather` : "Weather"}
          sub={
            <>
              Next 7 days · Weather by{" "}
              <a href="https://open-meteo.com" target="_blank" rel="noreferrer">
                Open-Meteo
              </a>
            </>
          }
        >
          {live.error || !live.data ? (
            <StateBlock compact loading={live.loading} error={live.error} onRetry={live.reload} />
          ) : (
            <ol className="wxstrip">
              {live.data.days.map((d) => (
                <li key={d.date}>
                  <span className="wxstrip__day">{day(d.date, { year: false }).split(" ").slice(0, 2).join(" ")}</span>
                  <WeatherGlyph code={d.weather_code} size={26} />
                  <span className="wxstrip__t">
                    <strong>{fahrenheit(d.weather_temperature_max)}</strong> {fahrenheit(d.weather_temperature_min)}
                  </span>
                  <span className="wxstrip__p">
                    {inches(d.weather_precipitation_sum)}
                    {isNum(d.precipitation_probability_max) && <> · {d.precipitation_probability_max}%</>}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </Panel>
      </div>

      <Panel className="policy" title="Cost of waste vs. stockout">
        <div className="policy__row">
          <div className="policy__slider">
            <input
              type="range"
              min={0.1}
              max={2}
              step={0.05}
              value={wcr}
              onChange={(e) => setWcr(parseFloat(e.target.value))}
              aria-label="Cost of waste relative to price"
              aria-valuetext={`Waste costs ${wcr.toFixed(2)} times price`}
            />
            <div className="policy__ends">
              <span>Order more</span>
              <span>Order less</span>
            </div>
          </div>
          <div className="policy__readout">
            <div className="policy__big">{wcr.toFixed(2)}×</div>
            <div className="policy__expl">
              An expired unit costs {wcr.toFixed(2)}× its price; a missed sale costs {MARGIN_RATIO.toFixed(2)}× in lost margin. So order
              up to the <strong>{Math.round(ratio * 100)}th percentile</strong> of forecast demand.
            </div>
          </div>
        </div>
        {data && (
          <div className="kpis" aria-live="polite">
            <div className="kpi">
              <span className="kpi__v">
                {int(totals.order)}
                {recs.loading && <span className="spinner spinner--inline" aria-label="updating" />}
              </span>
              <span className="kpi__l">units to order</span>
              {deltas && Math.abs(deltas.total) >= 0.5 && (
                <span key={`${data.waste_cost_ratio}`} className={`kpi__d ${deltas.total > 0 ? "up" : "down"}`}>
                  {deltas.total > 0 ? "▲" : "▼"} {int(Math.abs(deltas.total))} vs. {deltas.from.toFixed(2)}×
                </span>
              )}
            </div>
            <div className="kpi">
              <span className="kpi__v">{int(totals.expiring)}</span>
              <span className="kpi__l">units expiring tomorrow</span>
            </div>
            <div className="kpi">
              <span className="kpi__v kpi__v--risk">{totals.high}</span>
              <span className="kpi__l">high waste risk</span>
            </div>
            <div className="kpi">
              <span className="kpi__v">{totals.markdowns}</span>
              <span className="kpi__l">markdowns suggested</span>
            </div>
          </div>
        )}
      </Panel>

      <Panel
        flush
        title="Items"
        sub={data ? `${rows.length} of ${data.items.length} perishables. Click a column to sort.` : undefined}
        aside={
          data && (
            <div className="filters">
              <div className="chips" role="group" aria-label="Category">
                <button className={`chipbtn ${category === "all" ? "is-on" : ""}`} onClick={() => setCategory("all")}>
                  All <span className="chipbtn__n">{data.items.length}</span>
                </button>
                {categories.map(([c, n]) => (
                  <button key={c} className={`chipbtn ${category === c ? "is-on" : ""}`} onClick={() => setCategory(c)}>
                    {categoryLabel(c)} <span className="chipbtn__n">{n}</span>
                  </button>
                ))}
              </div>
              <select value={risk} onChange={(e) => setRisk(e.target.value as typeof risk)} aria-label="Filter by waste risk">
                <option value="all">Any risk</option>
                <option value="high">High risk</option>
                <option value="watch">Watch</option>
                <option value="low">Low risk</option>
                <option value="markdown">Has markdown</option>
              </select>
              <input type="search" placeholder="Find item" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Find item" />
              {store && (
                <a
                  className="btn btn--sm"
                  href={`/api/stores/${encodeURIComponent(store)}/plan.csv${qs({ waste_cost_ratio: wcr.toFixed(2) })}`}
                  download
                  title={`Full plan at a waste cost of ${wcr.toFixed(2)}× price`}
                >
                  Download plan (CSV)
                </a>
              )}
            </div>
          )
        }
      >
        {!data ? (
          <StateBlock
            loading={recs.loading}
            error={recs.error}
            onRetry={recs.reload}
            notFound={
              <>
                <p>
                  <strong>No order plan yet.</strong> The production model hasn't produced tomorrow's recommendations.
                </p>
                <p>
                  Run <code>python -m forecaster.pipeline.train_production</code> in <code>backend/</code>, then check again. The
                  live forecast above still works.
                </p>
              </>
            }
          />
        ) : rows.length === 0 ? (
          <StateBlock empty emptyText="No items match these filters." />
        ) : (
          <div className="tablewrap">
            <table className="table table--items">
              <thead>
                <tr>
                  {header("name", "Item", "")}
                  {header("category", "Category", "")}
                  {header("p50", "Forecast")}
                  {header("order_qty", "Order")}
                  {header("on_hand", "On hand")}
                  {header("expiring_tomorrow", "Expiring tmrw")}
                  {header("risk", "Waste risk", "")}
                  {header("plan", "Markdown plan", "")}
                  <th>Surplus plan</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(page * ITEMS_PER_PAGE, (page + 1) * ITEMS_PER_PAGE).map((i) => {
                  const d = deltas?.map.get(i.product_id);
                  return (
                    <tr key={i.product_id} className={`risk-row--${i.waste_risk}`}>
                      <td className="item">
                        <span className="item__name">{i.name ?? `Product ${i.product_id}`}</span>
                        <span className="item__id">#{i.product_id}</span>
                      </td>
                      <td className="cat">{categoryLabel(i.category)}</td>
                      <td className="num">{num(i.p50, 1)}</td>
                      <td className="num order">
                        <span key={`${i.product_id}-${data.waste_cost_ratio}`} className={d ? "flash" : undefined}>
                          {num(i.order_qty, 0)}
                        </span>
                        {deltas && (
                          <span className={`delta ${d === undefined ? "" : d > 0 ? "up" : "down"}`} aria-hidden={d === undefined}>
                            {d !== undefined && `${d > 0 ? "+" : "−"}${num(Math.abs(d), 0)}`}
                          </span>
                        )}
                      </td>
                      <td className="num">{num(i.on_hand, 0)}</td>
                      <td className="num">{i.expiring_tomorrow >= 0.5 ? num(i.expiring_tomorrow, 0) : <span className="muted">0</span>}</td>
                      <td>
                        <RiskBadge risk={i.waste_risk} />
                      </td>
                      <td className="mdplan" title={i.schedule_text ?? undefined}>
                        <MarkdownPlan item={i} />
                      </td>
                      <td className="muted">{SURPLUS_LABEL[i.surplus_action ?? "sell"]}{(i.donate_units ?? 0) > 0 ? ` (${num(i.donate_units, 0)})` : ""}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {data && rows.length > ITEMS_PER_PAGE && (
          <Pager page={page} pageSize={ITEMS_PER_PAGE} total={rows.length} onPage={setPage}
                 shown={Math.min(ITEMS_PER_PAGE, rows.length - page * ITEMS_PER_PAGE)} />
        )}
        {data && (
          <p className="note note--pad">
            Order = forecast quantile for the chosen cost ratio, minus fresh stock still sellable tomorrow. Waste risk compares units
            expiring tomorrow with P50 demand. Markdown plan: the smallest discount that clears the projected surplus before it
            expires, started only as early as needed, then donate what it can't clear. Discount response is this store's measured
            response when it has enough promotion days, otherwise the model's estimate from historical discounts. Hover a plan for
            the full schedule.
          </p>
        )}
      </Panel>

      <PromotionPanel promos={promos} items={data?.items ?? []} />
    </div>
  );
}

function PromotionPanel({ promos, items }: { promos: ReturnType<typeof useApi<PromotionExperiment[]>>; items: RecItem[] }) {
  const names = useMemo(() => new Map(items.map((i) => [i.product_id, i])), [items]);
  const list = [...(promos.data ?? [])].sort((a, b) => b.start_time.localeCompare(a.start_time));
  return (
    <Panel
      flush
      title="Promotion experiments"
      sub="Every discount day in the store's sheets is recorded with the forecast without the discount and what actually sold. After about 15 per category, discount plans use this store's measured response."
    >
      {promos.error || promos.loading || !list.length ? (
        <StateBlock
          compact
          loading={promos.loading && !promos.data}
          error={promos.error}
          onRetry={promos.reload}
          empty={!list.length}
          emptyText="No discount days in this store's sheets yet."
        />
      ) : (
        <div className="tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th>Item</th>
                <th className="num">Discount</th>
                <th>Window</th>
                <th className="num">Stock before</th>
                <th className="num">Forecast without</th>
                <th className="num">Actual sales</th>
                <th className="num">Observed lift</th>
              </tr>
            </thead>
            <tbody>
              {list.map((p) => {
                const it = names.get(p.product_id);
                return (
                  <tr key={p.experiment_id}>
                    <td className="item">
                      <span className="item__name">{it?.name ?? `Product ${p.product_id}`}</span>
                      <span className="item__id">#{p.product_id}</span>
                    </td>
                    <td className="num">
                      <Sticker depth={p.discount} />
                    </td>
                    <td>
                      {day(p.start_time.slice(0, 10), { year: false })}, {p.start_time.slice(11, 16)}–{p.end_time.slice(11, 16)}
                    </td>
                    <td className="num">{num(p.inventory_before, 0)}</td>
                    <td className="num">{num(p.forecast_without_promotion, 1)}</td>
                    <td className="num">{isNum(p.actual_sales) ? num(p.actual_sales, 1) : <span className="pending">awaiting outcome</span>}</td>
                    <td className="num">{isNum(p.observed_lift) ? spct(p.observed_lift, 0) : <span className="pending">measuring</span>}</td>
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


interface BriefingResp {
  text: string;
  provider: string;
}

/** Four short bullets written from the computed plan; each opens the ledger behind it. */
function Briefing({ store }: { store: string }) {
  const b = useApi<BriefingResp>(`/api/stores/${encodeURIComponent(store)}/briefing`);
  const bullets = (b.data?.text ?? "")
    .split("\n")
    .map((l) => l.replace(/^\s*[-•*]\s*/, "").trim())
    .filter(Boolean)
    .slice(0, 4);

  return (
    <Panel title="Morning briefing">
      {!b.data ? (
        <StateBlock compact loading={b.loading} error={b.error} onRetry={b.reload} />
      ) : (
        <ul className="briefing">
          {bullets.map((t, k) => (
            <li key={k}>
              <a href="#/ledger">{t}</a>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

const SURPLUS_LABEL: Record<string, string> = {
  sell: "Sells through",
  markdown_later: "Discount later",
  markdown: "Markdown clears it",
  markdown_then_donate: "Markdown, then donate",
  donate: "Donate",
};

function hasMarkdown(i: RecItem): boolean {
  return i.markdown > 0 || (i.markdown_schedule?.length ?? 0) > 0;
}

/** First markdown step when nothing is discounted tomorrow. */
/** Steps for separate shelf cohorts can share a date and depth; show each (date, depth) once. */
function mergedSteps(steps: MarkdownStep[]): MarkdownStep[] {
  const m = new Map<string, MarkdownStep>();
  for (const st of steps) {
    const k = `${st.date}|${st.discount}`;
    const prev = m.get(k);
    m.set(k, prev ? { ...prev, units: prev.units + st.units } : { ...st });
  }
  return [...m.values()].sort((a, b) => a.date.localeCompare(b.date) || a.discount - b.discount);
}

function liftSourceLabel(src: string | null | undefined): string | null {
  if (!src || src === "none") return null;
  if (src.includes("measured")) return "store-measured";
  if (src.includes("trained model")) return "trained model";
  if (src.includes("model")) return "model estimate";
  if (src.includes("default")) return "default";
  return src;
}

/** Compact schedule, e.g. "20% from 27 Sep → donate ~6 28 Sep"; the full sentence is the cell's tooltip. */
function MarkdownPlan({ item: i }: { item: RecItem }) {
  const steps = mergedSteps(i.markdown_schedule ?? []);
  const donate = (i.donate_units ?? 0) >= 0.5;
  if (!steps.length && !donate) return <span className="muted">—</span>;
  const src = steps.length ? liftSourceLabel(i.lift_source) : null;
  return (
    <>
      <span className="mdplan__line">
        {steps.map((st, k) => (
          <span key={`${st.date}-${st.discount}`}>
            {k > 0 && " → "}
            <strong>{Math.round(st.discount * 100)}%</strong> from {shortDay(st.date)}
          </span>
        ))}
        {donate && (
          <span className="mdplan__donate">
            {steps.length > 0 && " → "}donate ~{num(i.donate_units, 0)}
            {i.donate_date && ` ${shortDay(i.donate_date)}`}
          </span>
        )}
      </span>
      {src && <span className="mdplan__src">{src}</span>}
      {steps.length > 0 && (i.money_kept_vs_no_action ?? 0) > 0.5 && (
        <span className="mdplan__src">keeps {num(i.money_kept_vs_no_action, 0)} more than no discount</span>
      )}
    </>
  );
}
