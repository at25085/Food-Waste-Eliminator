import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { postJSON, qs, useApi } from "../api";
import type { Meta, Metric, ModelHealth as MH, ModelVersion, ProductionReport, Store } from "../types";
import { Meter, Panel, Segmented, StateBlock, StatusChip } from "../components/ui";
import { useToast } from "../components/toast";
import DataSources from "../components/DataSources";
import { C, axisProps } from "../chartTheme";
import { day, humanize, int, isNum, num, pct, spct, wape } from "../format";

type Ctx = "production" | "replay";

export default function ModelHealth(props: { stores: Store[]; store: string | null; setStore: (s: string) => void; meta?: Meta }) {
  const [ctx, setCtx] = useState<Ctx>("production");
  const health = useApi<MH>(`/api/model-health${qs({ context: ctx })}`);
  const report = useApi<ProductionReport>("/api/production/report");

  return (
    <div className="screen">
      <header className="screen__head">
        <div>
          <h1 className="display">Model health</h1>
          <p className="screen__lede">Which model is serving, how accurate it is on data it never saw, and what it's allowed to learn from.</p>
        </div>
        <Segmented<Ctx>
          label="Model context"
          value={ctx}
          onChange={setCtx}
          options={[
            { value: "production", label: "Production" },
            { value: "replay", label: "Backtest replay" },
          ]}
        />
      </header>

      <ChampionPanel health={health} ctx={ctx} onShowReplay={() => setCtx("replay")} meta={props.meta} />

      {health.data && (
        <blockquote className="principle">
          <p>{health.data.principle}</p>
        </blockquote>
      )}

      {ctx === "production" && <VersionsPanel onChanged={health.reload} />}

      <section className="block">
        <h2 className="h2">Holdout report</h2>
        {!report.data ? (
          <Panel>
            <StateBlock
              loading={report.loading}
              error={report.error}
              onRetry={report.reload}
              notFound={
                <p>
                  <strong>Pipeline not run yet.</strong> The production report (holdout test, baselines, simulated waste, feature
                  importance) appears after <code>python -m forecaster.pipeline.train_production</code> finishes.
                </p>
              }
            />
          </Panel>
        ) : (
          <Report r={report.data} />
        )}
      </section>

      <DataSources stores={props.stores} store={props.store} setStore={props.setStore} onUploaded={health.reload} />
    </div>
  );
}

function ChampionPanel({ health, ctx, onShowReplay, meta }: { health: ReturnType<typeof useApi<MH>>; ctx: Ctx; onShowReplay: () => void; meta?: Meta }) {
  if (!health.data) {
    return (
      <Panel title="Serving champion">
        <StateBlock
          loading={health.loading}
          error={health.error}
          onRetry={health.reload}
          notFound={
            <>
              <p>
                <strong>No {ctx === "production" ? "production" : "backtest"} champion yet.</strong>{" "}
                {ctx === "production"
                  ? "The production model is trained by the pipeline and hasn't been registered."
                  : "The replay hasn't registered its bootstrap model yet."}
              </p>
              {ctx === "production" && (
                <button className="btn btn--primary btn--sm" onClick={onShowReplay}>
                  Show the backtest champion instead
                </button>
              )}
            </>
          }
        />
      </Panel>
    );
  }
  const h = health.data;
  const train = h.metrics?.train;
  const test = h.metrics?.test ?? h.metrics?.eval;
  const testLabel = h.metrics?.test ? "Test (holdout)" : "Evaluation window";
  const ratio = isNum(test?.wape) && isNum(train?.wape) && train!.wape! > 0 ? test!.wape! / train!.wape! : null;
  const guard = meta?.thresholds.overfit_ratio_warning ?? 1.5;
  const maxW = Math.max(train?.wape ?? 0, test?.wape ?? 0, 0.01);

  return (
    <Panel
      title="Serving champion"
      sub={ctx === "replay" ? "Backtest context: models trained during the historical replay." : "Production context: the model behind today's plan."}
    >
      <div className="champ">
        <div className="champ__id">
          <code className="ver ver--xl">{h.champion}</code>
          <p className="champ__reason">{h.decision_reason}</p>
          <dl className="facts facts--tight">
            <div>
              <dt>Training data</dt>
              <dd>
                {day(h.training_range[0])} to {day(h.training_range[1])}
              </dd>
            </div>
            <div>
              <dt>Trained on</dt>
              <dd>{h.trained_on}</dd>
            </div>
            <div>
              <dt>Feature schema</dt>
              <dd>
                <code>{h.feature_schema_version}</code>
              </dd>
            </div>
          </dl>
        </div>

        <div className="champ__acc">
          <h3 className="h3">Error (WAPE, lower is better)</h3>
          <div className="wbars">
            <WBar label={testLabel} m={test} max={maxW} strong />
            <WBar label="Training data" m={train} max={maxW} />
          </div>
          <p className="note">
            {ratio !== null && (
              <>
                Unseen-data error is {num(ratio, 2)}× training error{" "}
                {ratio > guard ? `— above the ${guard}× overfit guard.` : `(guard: ${guard}×).`}{" "}
              </>
            )}
            {isNum(test?.bias) && (
              <>
                Bias {spct(test!.bias)}: the model {test!.bias! < 0 ? "under" : "over"}-forecasts on average
                {test!.bias! < 0 ? " (risk of stockouts)." : " (risk of waste)."}
              </>
            )}
            {isNum(h.metrics?.p80_coverage) && <> P80 covered {pct(h.metrics!.p80_coverage)} of actual sales.</>}
          </p>
        </div>

        <div className="champ__learn">
          <h3 className="h3">Store-specific learning</h3>
          <div className="obs">
            <span className="obs__n">
              {h.new_first_party_days} <span className="obs__of">/ {h.retrain_threshold_days}</span>
            </span>
            <span className="obs__l">new real store observation days</span>
          </div>
          <Meter value={h.new_first_party_days} max={h.retrain_threshold_days} label="New observation days toward retraining threshold" />
          <p className={`elig ${h.retrain_eligible ? "elig--yes" : ""}`}>
            {h.retrain_eligible
              ? "Eligible: the next weekly evaluation will train a challenger."
              : `Not eligible yet: ${h.retrain_threshold_days - h.new_first_party_days} more days of first-party data before a challenger is trained.`}
          </p>
          {meta && (
            <p className="note">
              A challenger is promoted only if WAPE improves ≥{pct(meta.thresholds.min_global_wape_improvement, 0)}, no major category
              worsens &gt;{pct(meta.thresholds.max_category_wape_degradation, 0)}, and it beats both naive baselines.
            </p>
          )}
        </div>
      </div>
    </Panel>
  );
}

function WBar({ label, m, max, strong }: { label: string; m?: Metric; max: number; strong?: boolean }) {
  const w = m?.wape ?? null;
  return (
    <div className={`wbar ${strong ? "wbar--strong" : ""}`}>
      <span className="wbar__l">{label}</span>
      <span className="wbar__track">
        <span className="wbar__fill" style={{ width: `${isNum(w) ? (w / max) * 100 : 0}%` }} />
      </span>
      <span className="wbar__v">{wape(w)}</span>
      <span className="wbar__n">{m ? `${int(m.n)} rows` : ""}</span>
    </div>
  );
}

const METHODS: { key: keyof ProductionReport["test"]; label: string; ours?: boolean }[] = [
  { key: "pred", label: "Our model", ours: true },
  { key: "pred_no_traffic", label: "Our model, traffic inputs removed" },
  { key: "pred_no_weather", label: "Retrained without weather" },
  { key: "rolling_mean_28", label: "28-day average" },
  { key: "seasonal_naive_7", label: "Same weekday last week" },
];

function Report({ r }: { r: ProductionReport }) {
  const [seg, setSeg] = useState<"category" | "store" | "weekday">("category");
  const [allFeat, setAllFeat] = useState(false);
  const rows = METHODS.map((m) => ({ ...m, metric: r.test[m.key] as Metric | undefined })).filter((m) => m.metric && isNum(m.metric.wape));
  const chart = rows.map((m) => ({ label: m.label, wape: m.metric!.wape!, ours: !!m.ours }));
  const model = r.test.pred;

  const effect = (other: Metric | undefined, what: string, how: string) => {
    const wape = (v: number) => `${(v * 100).toFixed(2)}%`;
    if (!other || !isNum(other.wape) || !isNum(model.wape)) return null;
    const d = other.wape - model.wape; // positive = feature helps
    const pts = Math.abs(d * 100).toFixed(2);
    if (Math.abs(d) < 0.0005) return `${what} made no measurable difference (${wape(model.wape)} with, ${wape(other.wape)} ${how}).`;
    if (d > 0) return `${what} helped: WAPE ${wape(model.wape)} with vs. ${wape(other.wape)} ${how} (${pts} pts).`;
    return `${what} did not help on this holdout: WAPE ${wape(model.wape)} with vs. ${wape(other.wape)} ${how}. We report it as measured.`;
  };
  const baseBest = Math.min(r.test.seasonal_naive_7?.wape ?? Infinity, r.test.rolling_mean_28?.wape ?? Infinity);

  const segRows: (Metric & { segment: string })[] =
    seg === "category"
      ? (r.test.by_category ?? []).map((x) => ({ ...x, segment: x.category }))
      : seg === "store"
        ? (r.test.by_store ?? []).map((x) => ({ ...x, segment: x.store_id }))
        : sortWeekdays((r.test.by_weekday ?? []).map((x) => ({ ...x, segment: x.weekday })));

  const feats = (r.feature_importance ?? []).slice(0, allFeat ? 25 : 12).map((f) => ({ ...f, label: humanize(f.feature) }));
  const sim = r.simulation;

  return (
    <>
      <div className="grid-2 grid-2--wide-left">
        <Panel
          title="Does the model beat simple rules?"
          sub={`Holdout ${day(r.holdout[0])} to ${day(r.holdout[1])}, never seen in training. Model ${r.version}.`}
        >
          <div className="chart" style={{ height: 40 + chart.length * 38 }}>
            <ResponsiveContainer>
              <BarChart data={chart} layout="vertical" margin={{ top: 4, right: 56, bottom: 4, left: 8 }} barCategoryGap={8}>
                <CartesianGrid horizontal={false} stroke={C.grid} />
                <XAxis type="number" {...axisProps} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} domain={[0, "auto"]} />
                <YAxis type="category" dataKey="label" width={210} {...axisProps} tick={{ fill: C.ink2, fontSize: 12 }} axisLine={false} />
                <Tooltip cursor={{ fill: C.bandA }} formatter={(v) => [wape(v as number), "WAPE"]} />
                <Bar dataKey="wape" radius={[0, 4, 4, 0]} isAnimationActive={false}>
                  {chart.map((d) => (
                    <Cell key={d.label} fill={d.ours ? C.spruce : C.neutralBar} />
                  ))}
                  <LabelList dataKey="wape" position="right" formatter={(v) => wape(v as number)} fill={C.ink} fontSize={12} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <ul className="findings">
            {isNum(model.wape) && isFinite(baseBest) && (
              <li>
                {model.wape < baseBest
                  ? `Beats the best naive baseline by ${((1 - model.wape / baseBest) * 100).toFixed(0)}% (relative WAPE).`
                  : "Does not beat the best naive baseline on this holdout."}
              </li>
            )}
            {effect(r.test.pred_no_weather, "Weather", "without") && <li>{effect(r.test.pred_no_weather, "Weather", "without")}</li>}
            {effect(r.test.pred_no_traffic, "Customer traffic", "with traffic inputs removed") && (
              <li>{effect(r.test.pred_no_traffic, "Customer traffic", "with traffic inputs removed")}</li>
            )}
          </ul>
        </Panel>

        <Panel title="Numbers behind the chart">
          <table className="table table--compact">
            <thead>
              <tr>
                <th>Method</th>
                <th className="num">WAPE</th>
                <th className="num">MAE</th>
                <th className="num">Bias</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((m) => (
                <tr key={m.key} className={m.ours ? "is-ours" : undefined}>
                  <td>{m.label}</td>
                  <td className="num">{wape(m.metric!.wape)}</td>
                  <td className="num">{num(m.metric!.mae, 2)}</td>
                  <td className="num">{spct(m.metric!.bias)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="stat">
            <span className="stat__v">{pct(r.p80_coverage, 0)}</span>
            <span className="stat__l">
              of product-days sold at or below the P80 forecast (target 80%).{" "}
              {isNum(r.p80_coverage) &&
                (Math.abs(r.p80_coverage - 0.8) <= 0.03
                  ? "Well calibrated."
                  : r.p80_coverage < 0.8
                    ? "P80 runs low: order sizing may under-stock."
                    : "P80 runs high: order sizing may over-stock.")}
            </span>
          </div>
        </Panel>
      </div>

      <div className="grid-2">
        <Panel
          flush
          title="Error by segment"
          aside={
            <Segmented
              size="sm"
              label="Segment"
              value={seg}
              onChange={setSeg}
              options={[
                { value: "category", label: "Category" },
                { value: "store", label: "Store" },
                { value: "weekday", label: "Weekday" },
              ]}
            />
          }
        >
          <SegmentTable rows={segRows} />
        </Panel>

        <Panel title="What the model relies on" sub="Share of total gain, top features" aside={
          (r.feature_importance?.length ?? 0) > 12 && (
            <button className="btn btn--ghost btn--sm" onClick={() => setAllFeat((a) => !a)}>
              {allFeat ? "Show top 12" : "Show top 25"}
            </button>
          )
        }>
          {feats.length ? (
            <div className="chart" style={{ height: 24 + feats.length * 22 }}>
              <ResponsiveContainer>
                <BarChart data={feats} layout="vertical" margin={{ top: 0, right: 48, bottom: 0, left: 4 }} barCategoryGap={3}>
                  <XAxis type="number" hide domain={[0, "auto"]} />
                  <YAxis type="category" dataKey="label" width={200} {...axisProps} tick={{ fill: C.ink2, fontSize: 11 }} axisLine={false} interval={0} />
                  <Tooltip cursor={{ fill: C.bandA }} formatter={(v) => [pct(v as number), "Share of gain"]} />
                  <Bar dataKey="share" fill={C.spruce} radius={[0, 3, 3, 0]} isAnimationActive={false}>
                    <LabelList dataKey="share" position="right" formatter={(v) => pct(v as number)} fill={C.ink2} fontSize={11} />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <StateBlock empty compact emptyText="No feature importance in this report." />
          )}
        </Panel>
      </div>

      {sim && (
        <Panel
          className="simulated"
          title={
            <>
              Waste and lost sales <span className="simtag">Simulated</span>
            </>
          }
          sub={sim.label}
        >
          <div className="simgrid">
            <SimCompare label="Units wasted" model={sim.totals.model_waste} base={sim.totals.baseline_waste} lowerBetter />
            <SimCompare label="Sales lost to stockouts" model={sim.totals.model_lost} base={sim.totals.baseline_lost} lowerBetter />
            <SimCompare label="Units sold" model={sim.totals.model_sold} base={sim.totals.baseline_sold} />
          </div>
          <p className="note">
            Model policy orders the cost-ratio quantile of the forecast; the comparison policy orders "same weekday last week" plus 10%
            safety stock. Shelf lives:{" "}
            {Object.entries(sim.shelf_life_days)
              .map(([k, v]) => `${k} ${v} d`)
              .join(", ")}
            . Waste cost ratio {sim.waste_cost_ratio}.
          </p>
          {sim.by_category?.length > 0 && (
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Category</th>
                  <th className="num">Wasted, model</th>
                  <th className="num">Wasted, naive</th>
                  <th className="num">Lost, model</th>
                  <th className="num">Lost, naive</th>
                </tr>
              </thead>
              <tbody>
                {sim.by_category.map((c) => (
                  <tr key={String(c.category)}>
                    <td>{String(c.category)}</td>
                    <td className="num">{int(c.model_waste as number)}</td>
                    <td className="num">{int(c.baseline_waste as number)}</td>
                    <td className="num">{int(c.model_lost as number)}</td>
                    <td className="num">{int(c.baseline_lost as number)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      )}

      {r.weather_cross_check && Object.keys(r.weather_cross_check).length > 0 && (
        <Panel
          flush
          title="Weather cross-check"
          sub="The dataset's own recorded precipitation vs. Open-Meteo reanalysis (ERA5). Kept side by side, never merged; dataset weather is never a model feature because it isn't known at prediction time."
        >
          <div className="tablewrap">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Store</th>
                  <th className="num">Days compared</th>
                  <th className="num">Precipitation correlation</th>
                  <th className="num">Mean abs. difference</th>
                  <th className="num">Wet/dry day agreement</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(r.weather_cross_check).map(([s, x]) => (
                  <tr key={s}>
                    <td>{s.replace("_", " ")}</td>
                    <td className="num">{int(x.days_compared)}</td>
                    <td className="num">{num(x.precipitation_corr, 2)}</td>
                    <td className="num">{isNum(x.precipitation_mean_abs_diff_mm) ? `${num(x.precipitation_mean_abs_diff_mm, 2)} mm` : "–"}</td>
                    <td className="num">{pct(x.wet_day_agreement, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </>
  );
}

function SimCompare({ label, model, base, lowerBetter }: { label: string; model?: number; base?: number; lowerBetter?: boolean }) {
  const max = Math.max(model ?? 0, base ?? 0, 1);
  const better = isNum(model) && isNum(base) ? (lowerBetter ? model < base : model > base) : null;
  const change = isNum(model) && isNum(base) && base > 0 ? model / base - 1 : null;
  return (
    <div className="simc">
      <div className="simc__l">{label}</div>
      <div className="simc__row">
        <span>Model</span>
        <span className="simc__track">
          <span className="simc__fill simc__fill--model" style={{ width: `${((model ?? 0) / max) * 100}%` }} />
        </span>
        <span className="num">{int(model)}</span>
      </div>
      <div className="simc__row">
        <span>Naive</span>
        <span className="simc__track">
          <span className="simc__fill" style={{ width: `${((base ?? 0) / max) * 100}%` }} />
        </span>
        <span className="num">{int(base)}</span>
      </div>
      {change !== null && <div className={`simc__d ${better ? "good" : "bad"}`}>{spct(change, 0)} vs. naive</div>}
    </div>
  );
}

const WEEK = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export function sortWeekdays<T extends { segment: string }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => WEEK.indexOf(a.segment) - WEEK.indexOf(b.segment));
}

export function SegmentTable({ rows }: { rows: (Metric & { segment: string })[] }) {
  if (!rows.length) return <StateBlock empty compact emptyText="No segment data." />;
  const max = Math.max(...rows.map((r) => r.wape ?? 0), 0.01);
  return (
    <div className="tablewrap">
      <table className="table table--compact">
        <thead>
          <tr>
            <th>Segment</th>
            <th className="num">Rows</th>
            <th>WAPE</th>
            <th className="num">MAE</th>
            <th className="num">Bias</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.segment}>
              <td>{r.segment.replace(/_(\d)$/, " $1")}</td>
              <td className="num muted">{int(r.n)}</td>
              <td className="barcell">
                <span className="barcell__track">
                  <span className="barcell__fill" style={{ width: `${((r.wape ?? 0) / max) * 100}%` }} />
                </span>
                <span className="barcell__v">{wape(r.wape)}</span>
              </td>
              <td className="num">{num(r.mae, 2)}</td>
              <td className={`num ${isNum(r.bias) && Math.abs(r.bias) > 0.05 ? "warn" : ""}`}>{spct(r.bias)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Every production version ever trained. Versions are immutable; rollback only re-points the
 *  champion and writes a history row, so it can itself be rolled back. */
function VersionsPanel({ onChanged }: { onChanged: () => void }) {
  const versions = useApi<ModelVersion[]>("/api/models?context=production");
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);

  async function rollback(v: string) {
    setBusy(v);
    try {
      const r = await postJSON<{ champion: string; changed: boolean; previous?: string }>(
        `/api/models/${encodeURIComponent(v)}/rollback?context=production`, {});
      toast({ kind: "info", title: r.changed ? `Champion is now ${r.champion}` : `${v} is already champion`,
              body: r.changed ? `Rolled back from ${r.previous}. Nothing was deleted.` : undefined });
      versions.reload();
      onChanged();
    } catch (e) {
      toast({ kind: "error", title: "Rollback failed", body: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Panel title="Model versions" sub="Immutable. Rolling back re-points the champion; no model is overwritten or deleted.">
      {!versions.data ? (
        <StateBlock loading={versions.loading} error={versions.error} onRetry={versions.reload} compact />
      ) : versions.data.length === 0 ? (
        <StateBlock empty compact emptyText="No production versions yet." />
      ) : (
        <table className="table table--compact">
          <thead>
            <tr><th>Version</th><th>Status</th><th>Trained through</th><th>Test WAPE</th><th>Reason</th><th /></tr>
          </thead>
          <tbody>
            {versions.data.map((m) => (
              <tr key={m.version}>
                <td><code className="ver">{m.version}</code></td>
                <td><StatusChip status={m.promotion_status} /></td>
                <td>{m.training_end ? day(m.training_end) : "—"}</td>
                <td>{isNum(m.metrics?.test?.wape) ? wape(m.metrics!.test!.wape) : "—"}</td>
                <td className="muted">{m.decision_reason ?? ""}</td>
                <td>
                  {m.promotion_status !== "champion" && (
                    <button className="btn btn--small" disabled={busy !== null} onClick={() => rollback(m.version)}>
                      {busy === m.version ? "Rolling back…" : "Make champion"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}
