import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts";
import { qs, useApi } from "../api";
import type { Meta, ModelVersion, RetrainRun, SegmentMetric, Timeseries, TimeseriesDay } from "../types";
import { Check, DecisionChip, Panel, Segmented, StateBlock, StatusChip } from "../components/ui";
import { useToast } from "../components/toast";
import { SegmentTable, sortWeekdays } from "./ModelHealth";
import { C, axisProps } from "../chartTheme";
import { day, int, isNum, pct, shortDay, spct, wape } from "../format";

const SPEEDS = { slow: 320, normal: 140, fast: 50 } as const;
type Speed = keyof typeof SPEEDS;
const BIAS_WARN = 0.05; // settings.bias_warning_threshold (not exposed by /api/meta)
const OVER = "#B8662A"; // over-forecast → waste (warm pole)
const UNDER = "#3F6FA0"; // under-forecast → stockouts (cool pole)

export default function LearningLoop({ meta }: { meta?: Meta }) {
  const ts = useApi<Timeseries>("/api/metrics/timeseries?context=replay");
  const runs = useApi<RetrainRun[]>("/api/retrain-runs?context=replay");
  const models = useApi<ModelVersion[]>("/api/models?context=replay");
  const [updated, setUpdated] = useState<Date>(new Date());

  const refresh = () => {
    ts.reload();
    runs.reload();
    models.reload();
    setUpdated(new Date());
  };

  const days = ts.data?.days ?? [];
  const runList = useMemo(() => [...(runs.data ?? [])].sort((a, b) => a.as_of_date.localeCompare(b.as_of_date)), [runs.data]);

  // Playback: cursor = index of the last visible day; null = show everything.
  const [cursor, setCursor] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<Speed>("normal");
  useEffect(() => {
    if (!playing) return;
    const id = window.setInterval(() => {
      setCursor((c) => {
        const next = (c ?? -1) + 1;
        if (next >= days.length - 1) {
          setPlaying(false);
          return days.length - 1;
        }
        return next;
      });
    }, SPEEDS[speed]);
    return () => window.clearInterval(id);
  }, [playing, speed, days.length]);

  const reduceMotion = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const togglePlay = () => {
    if (playing) return setPlaying(false);
    if (cursor === null || cursor >= days.length - 1) setCursor(0);
    setPlaying(true);
  };

  const curIdx = cursor ?? days.length - 1;
  const curDay: TimeseriesDay | undefined = days[curIdx];
  const curDate = curDay?.forecast_date ?? "";
  const visibleRuns = cursor === null ? runList : runList.filter((r) => r.as_of_date <= curDate);

  // Toasts as the playhead crosses a decision.
  const toast = useToast();
  const prevDate = useRef<string>("");
  useEffect(() => {
    if (!playing || !curDate) {
      prevDate.current = curDate;
      return;
    }
    const crossed = runList.filter((r) => r.as_of_date > prevDate.current && r.as_of_date <= curDate && r.decision !== "skipped");
    for (const r of crossed) {
      const ch = r.comparison?.challenger.global.wape;
      const cw = r.comparison?.champion.global.wape;
      toast(
        r.decision === "promoted"
          ? {
              kind: "promoted",
              title: `${r.candidate_version} promoted`,
              body: `${r.reason}. Serves from the next day.`,
            }
          : {
              kind: "rejected",
              title: `Challenger ${r.candidate_version ?? ""} rejected`,
              body: r.reason || (isNum(ch) && isNum(cw) ? `WAPE ${wape(ch)} vs champion ${wape(cw)}` : undefined),
            },
        reduceMotion ? 7000 : 4200,
      );
    }
    prevDate.current = curDate;
  }, [curDate, playing, runList, toast, reduceMotion]);

  // Flip the serving badge only when the version actually changes during playback.
  const [flips, setFlips] = useState(0);
  const lastVersion = useRef<string | undefined>(undefined);
  useEffect(() => {
    const v = curDay?.version;
    if (playing && lastVersion.current && v && v !== lastVersion.current && !reduceMotion) setFlips((f) => f + 1);
    lastVersion.current = v;
  }, [curDay?.version, playing, reduceMotion]);

  const tally = useMemo(() => {
    const t = { promoted: 0, rejected: 0, skipped: 0 };
    for (const r of visibleRuns) if (r.decision in t) t[r.decision as keyof typeof t]++;
    return t;
  }, [visibleRuns]);

  const loading = ts.loading && !ts.data;

  return (
    <div className="screen">
      <header className="screen__head">
        <div>
          <h1 className="display">Learning loop</h1>
          <p className="screen__lede">
            Log a prediction, attach what actually sold, measure the error, train a challenger, and promote it only if it wins on
            data it never saw.
          </p>
        </div>
        <div className="refresh">
          <button className="btn btn--ghost btn--sm" onClick={refresh} disabled={ts.loading}>
            {ts.loading ? "Refreshing…" : "Refresh data"}
          </button>
          <span className="muted">Updated {updated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
        </div>
      </header>

      <p className="replaylabel">
        <svg viewBox="0 0 20 20" aria-hidden>
          <path d="M3 10a7 7 0 1 0 2.1-5" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
          <path d="M3 3v4h4" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span>
          <strong>Historical replay (backtest)</strong> — Jan–Jun 2024, forecasts made with the weather forecast actually issued the day
          before. Not live customers.
        </span>
      </p>

      {!ts.data || !days.length ? (
        <Panel>
          <StateBlock
            loading={loading}
            error={ts.error}
            onRetry={refresh}
            empty={!!ts.data && !days.length}
            emptyText={
              <>
                <strong>No replay days yet.</strong> The backtest writes one day at a time; if it's running, press Refresh data in a
                minute.
              </>
            }
          />
        </Panel>
      ) : (
        <>
          <div className="player">
            <button className={`play ${playing ? "is-playing" : ""}`} onClick={togglePlay} aria-label={playing ? "Pause replay" : "Play replay"}>
              {playing ? (
                <svg viewBox="0 0 20 20" aria-hidden>
                  <rect x="5" y="4" width="3.5" height="12" rx="1" />
                  <rect x="11.5" y="4" width="3.5" height="12" rx="1" />
                </svg>
              ) : (
                <svg viewBox="0 0 20 20" aria-hidden>
                  <path d="M6 4l10 6-10 6z" />
                </svg>
              )}
              <span>{playing ? "Pause" : cursor !== null && cursor < days.length - 1 ? "Resume" : "Play replay"}</span>
            </button>

            <div className="scrub">
              <input
                type="range"
                min={0}
                max={days.length - 1}
                value={curIdx}
                onChange={(e) => {
                  setPlaying(false);
                  setCursor(parseInt(e.target.value, 10));
                }}
                aria-label="Replay day"
                aria-valuetext={day(curDate)}
              />
              <div className="scrub__meta">
                <span className="scrub__date">{day(curDate)}</span>
                <span className="muted">
                  day {curIdx + 1} of {days.length}
                </span>
                {cursor !== null && (
                  <button
                    className="link"
                    onClick={() => {
                      setPlaying(false);
                      setCursor(null);
                    }}
                  >
                    Show all
                  </button>
                )}
              </div>
            </div>

            <Segmented<Speed>
              size="sm"
              label="Playback speed"
              value={speed}
              onChange={setSpeed}
              options={[
                { value: "slow", label: "Slow" },
                { value: "normal", label: "1×" },
                { value: "fast", label: "Fast" },
              ]}
            />

            <div className="serving" aria-live="polite">
              <span className="serving__l">Serving</span>
              <code key={flips} className={`serving__v ${flips > 0 ? "flip" : ""}`}>
                {curDay?.version ?? "–"}
              </code>
            </div>

            <div className="tally" aria-label="Retraining decisions so far">
              <span className="tally__i tally__i--promoted">
                <strong>{tally.promoted}</strong> promoted
              </span>
              <span className="tally__i tally__i--rejected">
                <strong>{tally.rejected}</strong> rejected
              </span>
              <span className="tally__i">
                <strong>{tally.skipped}</strong> skipped
              </span>
            </div>
          </div>

          <div className="loopgrid">
            <div className="loopgrid__charts">
              <Panel
                title="Daily forecast error"
                sub={
                  <>
                    WAPE = Σ|predicted − actual| ÷ Σ actual, across all products and stores. Shaded bands show which model version served
                    each day.
                  </>
                }
                aside={<ErrorLegend refWape={ts.data.reference_wape} mult={ts.data.drift_multiplier ?? 1.15} />}
              >
                <ErrorChart ts={ts.data} runs={runList} cursor={cursor} />
              </Panel>
              <Panel
                title="Bias"
                sub="Σ(predicted − actual) ÷ Σ actual. Above zero the model over-forecasts (waste risk); below zero it under-forecasts (stockout risk)."
                aside={
                  <div className="legend">
                    <span className="legend__i">
                      <span className="legend__sw" style={{ background: OVER }} /> Over-forecast
                    </span>
                    <span className="legend__i">
                      <span className="legend__sw" style={{ background: UNDER }} /> Under-forecast
                    </span>
                    <span className="legend__i">
                      <span className="legend__dash" /> ±{pct(BIAS_WARN, 0)} warning
                    </span>
                  </div>
                }
              >
                <BiasChart days={days} cursor={cursor} />
              </Panel>
            </div>

            <Panel className="feedpanel" title="Retraining decisions" sub="Weekly evaluation. A challenger is trained only once enough new days exist.">
              <DecisionFeed runs={visibleRuns} loading={runs.loading && !runs.data} error={runs.error} onRetry={runs.reload} meta={meta} following={cursor !== null} />
            </Panel>
          </div>
        </>
      )}

      <ModelsTable models={models} servingVersion={cursor !== null ? curDay?.version : undefined} />
      <Segments />
    </div>
  );
}

function ErrorLegend({ refWape, mult }: { refWape: number | null; mult: number }) {
  return (
    <div className="legend">
      <span className="legend__i">
        <span className="legend__ln" style={{ background: C.spruceLight, height: 1.5 }} /> Daily
      </span>
      <span className="legend__i">
        <span className="legend__ln" style={{ background: C.spruceMid }} /> 7-day
      </span>
      <span className="legend__i">
        <span className="legend__ln" style={{ background: C.spruce, height: 3 }} /> 30-day
      </span>
      {isNum(refWape) && (
        <span className="legend__i" title={`Reference WAPE ${wape(refWape)} (first 28 replay days) × ${mult}`}>
          <span className="legend__dash legend__dash--crit" /> Drift threshold
        </span>
      )}
      <span className="legend__i">
        <span className="legend__tri" /> Drift day
      </span>
    </div>
  );
}

interface Band {
  version: string;
  x1: string;
  x2: string;
  i: number;
}

function bands(days: TimeseriesDay[], upto: number): Band[] {
  const out: Band[] = [];
  for (let i = 0; i <= upto && i < days.length; i++) {
    const d = days[i];
    const last = out[out.length - 1];
    if (last && last.version === d.version) last.x2 = d.forecast_date;
    else out.push({ version: d.version, x1: d.forecast_date, x2: d.forecast_date, i: out.length });
  }
  return out;
}

function ErrorChart({ ts, runs, cursor }: { ts: Timeseries; runs: RetrainRun[]; cursor: number | null }) {
  const days = ts.days;
  const upto = cursor ?? days.length - 1;
  const curDate = days[upto]?.forecast_date ?? "";
  const dateSet = useMemo(() => new Set(days.map((d) => d.forecast_date)), [days]);
  const data = useMemo(
    () =>
      days.map((d, i) =>
        i <= upto
          ? { ...d, drift_mark: d.drift ? d.wape_30d : null }
          : { forecast_date: d.forecast_date, version: d.version, wape: null, wape_7d: null, wape_30d: null, drift_mark: null, bias: null },
      ),
    [days, upto],
  );
  const bs = bands(days, upto);
  const threshold = isNum(ts.reference_wape) ? ts.reference_wape * (ts.drift_multiplier ?? 1.15) : null;
  const rawMax = Math.max(...days.map((d) => d.wape ?? 0), threshold ?? 0) * 1.05;
  const step = rawMax > 0.4 ? 0.1 : 0.05;
  const maxY = Math.max(step, Math.ceil(rawMax / step) * step);
  const yTicks = Array.from({ length: Math.round(maxY / step) + 1 }, (_, i) => +(i * step).toFixed(2));
  const decisions = runs.filter((r) => r.decision !== "skipped" && r.as_of_date <= curDate && dateSet.has(r.as_of_date));

  return (
    <div className="chart" style={{ height: 300 }}>
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 22, right: 12, bottom: 0, left: -8 }} syncId="replay">
          {bs.map((b) => (
            <ReferenceArea
              key={`${b.version}-${b.x1}`}
              x1={b.x1}
              x2={b.x2}
              y1={0}
              y2={maxY}
              fill={b.i % 2 === 0 ? C.bandA : "#E2ECE8"}
              fillOpacity={1}
              stroke="none"
              ifOverflow="hidden"
              label={{ value: b.version, position: "insideTopLeft", fill: C.ink3, fontSize: 11, offset: 6 }}
            />
          ))}
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis dataKey="forecast_date" {...axisProps} tickFormatter={shortDay} minTickGap={36} />
          <YAxis {...axisProps} axisLine={false} domain={[0, maxY]} ticks={yTicks} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} width={48} allowDataOverflow />
          {threshold !== null && <ReferenceLine y={threshold} stroke={C.critical} strokeDasharray="5 4" strokeWidth={1.25} ifOverflow="extendDomain" />}
          {decisions.map((r) => (
            <ReferenceLine
              key={r.run_id}
              x={r.as_of_date}
              stroke={r.decision === "promoted" ? C.good : C.critical}
              strokeWidth={1.5}
              strokeDasharray={r.decision === "promoted" ? undefined : "3 3"}
              label={{ value: r.decision === "promoted" ? "▲" : "✕", position: "top", fill: r.decision === "promoted" ? C.good : C.critical, fontSize: 11 }}
            />
          ))}
          <Line dataKey="wape" stroke={C.spruceLight} strokeWidth={1.25} dot={false} isAnimationActive={false} connectNulls={false} />
          <Line dataKey="wape_7d" stroke={C.spruceMid} strokeWidth={1.75} dot={false} isAnimationActive={false} />
          <Line dataKey="wape_30d" stroke={C.spruce} strokeWidth={2.75} dot={false} isAnimationActive={false} />
          <Line
            dataKey="drift_mark"
            stroke="none"
            isAnimationActive={false}
            legendType="none"
            dot={(p: { cx?: number; cy?: number; index?: number; value?: unknown }) =>
              isNum(p.cx) && isNum(p.cy) && p.value !== null && p.value !== undefined ? (
                <path key={`dm-${p.index}`} d={`M${p.cx},${p.cy - 9} l4,-6 l-8,0 z`} fill={C.critical} />
              ) : (
                <g key={`dm-${p.index}`} />
              )
            }
            activeDot={false}
          />
          {cursor !== null && <ReferenceLine x={curDate} stroke={C.ink} strokeWidth={1} />}
          <Tooltip content={(p) => <ErrorTip {...p} />} isAnimationActive={false} />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

function ErrorTip(p: TooltipContentProps) {
  if (!p.active || !p.payload?.length) return null;
  const d = p.payload[0].payload as TimeseriesDay;
  if (!isNum(d.wape)) return null;
  return (
    <div className="tip">
      <div className="tip__h">
        {day(d.forecast_date)} <code className="ver">{d.version}</code>
      </div>
      <table>
        <tbody>
          <tr>
            <td>Daily WAPE</td>
            <td>{wape(d.wape)}</td>
          </tr>
          <tr>
            <td>7-day</td>
            <td>{wape(d.wape_7d)}</td>
          </tr>
          <tr>
            <td>30-day</td>
            <td>{wape(d.wape_30d)}</td>
          </tr>
          <tr>
            <td>Bias</td>
            <td>{spct(d.bias)}</td>
          </tr>
          <tr>
            <td>Units sold</td>
            <td>{int(d.actual)}</td>
          </tr>
        </tbody>
      </table>
      {d.drift && <div className="tip__warn">Drift: 30-day WAPE above threshold</div>}
    </div>
  );
}

function BiasChart({ days, cursor }: { days: TimeseriesDay[]; cursor: number | null }) {
  const upto = cursor ?? days.length - 1;
  const data = useMemo(() => days.map((d, i) => ({ forecast_date: d.forecast_date, bias: i <= upto ? d.bias : null, version: d.version })), [days, upto]);
  const rawM = Math.max(0.1, ...days.map((d) => Math.abs(d.bias ?? 0))) * 1.05;
  const bStep = rawM > 0.2 ? 0.1 : 0.05;
  const m = Math.ceil(rawM / bStep) * bStep;
  const bTicks = Array.from({ length: Math.round((2 * m) / bStep) + 1 }, (_, i) => +(-m + i * bStep).toFixed(2));
  return (
    <div className="chart" style={{ height: 150 }}>
      <ResponsiveContainer>
        <ComposedChart data={data} margin={{ top: 6, right: 12, bottom: 0, left: -8 }} syncId="replay" barCategoryGap={1}>
          <CartesianGrid vertical={false} stroke={C.grid} />
          <XAxis dataKey="forecast_date" {...axisProps} tickFormatter={shortDay} minTickGap={36} />
          <YAxis {...axisProps} axisLine={false} domain={[-m, m]} ticks={bTicks} tickFormatter={(v: number) => `${Math.round(v * 100)}%`} width={48} />
          <ReferenceLine y={BIAS_WARN} stroke={C.ink3} strokeDasharray="4 4" />
          <ReferenceLine y={-BIAS_WARN} stroke={C.ink3} strokeDasharray="4 4" />
          <Bar dataKey="bias" isAnimationActive={false}>
            {data.map((d) => (
              <Cell key={d.forecast_date} fill={(d.bias ?? 0) >= 0 ? OVER : UNDER} />
            ))}
          </Bar>
          <ReferenceLine y={0} stroke={C.ink2} strokeWidth={1} />
          <Tooltip
            cursor={{ fill: C.bandA }}
            isAnimationActive={false}
            content={(p: TooltipContentProps) => {
              if (!p.active || !p.payload?.length) return null;
              const d = p.payload[0].payload as { forecast_date: string; bias: number | null; version: string };
              if (!isNum(d.bias)) return null;
              return (
                <div className="tip">
                  <div className="tip__h">{day(d.forecast_date)}</div>
                  Bias {spct(d.bias)} ({d.bias >= 0 ? "over-forecast" : "under-forecast"})
                </div>
              );
            }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

function DecisionFeed(props: {
  runs: RetrainRun[];
  loading: boolean;
  error?: import("../api").ApiError;
  onRetry: () => void;
  meta?: Meta;
  following: boolean;
}) {
  const listRef = useRef<HTMLOListElement>(null);
  const n = props.runs.length;
  useEffect(() => {
    // Keep the newest decision in view while the replay plays.
    const el = listRef.current;
    if (el && props.following) el.scrollTop = el.scrollHeight;
  }, [n, props.following]);

  if (props.loading || props.error) return <StateBlock compact loading={props.loading} error={props.error} onRetry={props.onRetry} />;
  if (!n) return <StateBlock compact empty emptyText="No weekly evaluation has happened yet at this point in the replay." />;

  return (
    <ol className="feed" ref={listRef}>
      {props.runs.map((r, idx) => (
        <li key={r.run_id} className={`feed__i feed__i--${r.decision} ${idx === n - 1 && props.following ? "is-latest" : ""}`}>
          <div className="feed__top">
            <span className="feed__date">{day(r.as_of_date, { year: false })}</span>
            <DecisionChip decision={r.decision} />
            {r.candidate_version && <code className="ver">{r.candidate_version}</code>}
          </div>
          {r.decision === "skipped" ? (
            <p className="feed__why">
              {Math.max(0, r.new_observation_days)} new days since training; needs {props.meta?.thresholds.min_new_days_for_retrain ?? 14}. No
              challenger trained.
            </p>
          ) : (
            <RunDetail r={r} meta={props.meta} />
          )}
        </li>
      ))}
    </ol>
  );
}

function RunDetail({ r, meta }: { r: RetrainRun; meta?: Meta }) {
  const c = r.comparison;
  if (!c) return <p className="feed__why">{r.reason}</p>;
  const limit = meta?.thresholds.max_category_wape_degradation ?? 0.05;
  const need = meta?.thresholds.min_global_wape_improvement ?? 0.02;
  const cw = c.champion.global.wape;
  const nw = c.challenger.global.wape;
  const baselines = Object.entries(c.baselines ?? {});
  return (
    <div className="run">
      <p className="feed__why">{r.reason}</p>
      <div className="run__vs">
        <div>
          <span className="run__l">Champion {r.champion_version}</span>
          <span className="run__v">{wape(cw)}</span>
        </div>
        <div>
          <span className="run__l">Challenger</span>
          <span className={`run__v ${isNum(cw) && isNum(nw) && nw < cw ? "good" : "bad"}`}>{wape(nw)}</span>
        </div>
        <div>
          <span className="run__l">Change</span>
          <span className="run__v">{spct(-c.checks.global_improvement)}</span>
        </div>
      </div>
      <details className="run__more" open={r.decision === "rejected"}>
        <summary>Promotion checks on {c.eval_window ? `${shortDay(c.eval_window[0])}–${shortDay(c.eval_window[1])}` : "the evaluation window"}</summary>
        <ul className="checks">
          <Check ok={c.checks.global_improvement_ok}>
            Global WAPE improves ≥{pct(need, 0)} (got {spct(c.checks.global_improvement)})
          </Check>
          <Check ok={c.checks.category_ok}>No major category worsens &gt;{pct(limit, 0)}</Check>
        </ul>
        <table className="table table--mini">
          <thead>
            <tr>
              <th>Category</th>
              <th className="num">Champion</th>
              <th className="num">Challenger</th>
              <th className="num">Change</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(c.checks.category_regressions).map(([cat, deg]) => (
              <tr key={cat} className={deg > limit ? "is-bad" : undefined}>
                <td>{cat}</td>
                <td className="num">{wape(c.champion.by_category[cat]?.wape)}</td>
                <td className="num">{wape(c.challenger.by_category[cat]?.wape)}</td>
                <td className="num">
                  {spct(deg)} {deg > limit ? "✗" : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <ul className="checks">
          <Check ok={c.checks.beats_baselines}>
            Beats naive baselines ({baselines.map(([k, b]) => `${k === "seasonal_naive_7" ? "last week" : k === "rolling_mean_28" ? "28-day avg" : k} ${wape(b.global.wape)}`).join(", ")})
          </Check>
          <Check ok={c.checks.overfit_ok}>
            Overfit guard: eval/train WAPE {isNum(c.checks.overfit_ratio) ? `${c.checks.overfit_ratio.toFixed(2)}×` : "–"} ≤{" "}
            {meta?.thresholds.overfit_ratio_warning ?? 1.5}×
          </Check>
        </ul>
      </details>
    </div>
  );
}

function ModelsTable({ models, servingVersion }: { models: ReturnType<typeof useApi<ModelVersion[]>>; servingVersion?: string }) {
  const list = models.data ?? [];
  return (
    <Panel flush title="Model versions" sub="Every version is immutable. Only its status changes: champion, retired, or rejected.">
      {!list.length ? (
        <StateBlock
          compact
          loading={models.loading}
          error={models.error}
          onRetry={models.reload}
          empty={!!models.data}
          emptyText="No replay models registered yet."
        />
      ) : (
        <div className="tablewrap">
          <table className="table table--compact">
            <thead>
              <tr>
                <th>Version</th>
                <th>Status</th>
                <th>Trained on data through</th>
                <th className="num">Train WAPE</th>
                <th className="num">Unseen-data WAPE</th>
                <th>Decision</th>
              </tr>
            </thead>
            <tbody>
              {list.map((m) => {
                const test = m.metrics?.test ?? m.metrics?.eval;
                return (
                  <tr key={m.version} className={servingVersion === m.version ? "is-serving" : undefined}>
                    <td>
                      <code className="ver">{m.version}</code>
                      {servingVersion === m.version && <span className="serving-dot">serving at playhead</span>}
                    </td>
                    <td>
                      <StatusChip status={m.promotion_status} />
                    </td>
                    <td>{day(m.training_end)}</td>
                    <td className="num">{wape(m.metrics?.train?.wape)}</td>
                    <td className="num" title={m.metrics?.test ? "Full replay window" : "Two-week evaluation window"}>
                      {wape(test?.wape)}
                    </td>
                    <td className="wrap">{m.decision_reason}</td>
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

function Segments() {
  const [by, setBy] = useState<"category" | "store" | "weekday" | "model">("category");
  const [win, setWin] = useState<"7" | "30" | "365">("30");
  const seg = useApi<SegmentMetric[]>(`/api/metrics${qs({ context: "replay", window: win, by })}`);
  const rows = by === "weekday" ? sortWeekdays(seg.data ?? []) : (seg.data ?? []);
  return (
    <Panel
      flush
      title="Where the error is"
      sub="Replay predictions with outcomes, over the last days of the replay."
      aside={
        <div className="filters">
          <Segmented
            size="sm"
            label="Segment"
            value={by}
            onChange={setBy}
            options={[
              { value: "category", label: "Category" },
              { value: "store", label: "Store" },
              { value: "weekday", label: "Weekday" },
              { value: "model", label: "Model version" },
            ]}
          />
          <Segmented
            size="sm"
            label="Window"
            value={win}
            onChange={setWin}
            options={[
              { value: "7", label: "7 days" },
              { value: "30", label: "30 days" },
              { value: "365", label: "Whole replay" },
            ]}
          />
        </div>
      }
    >
      {!seg.data ? (
        <StateBlock compact loading={seg.loading} error={seg.error} onRetry={seg.reload} />
      ) : !rows.length ? (
        <StateBlock compact empty emptyText="No outcomes in this window yet." />
      ) : (
        <SegmentTable rows={rows} />
      )}
    </Panel>
  );
}
