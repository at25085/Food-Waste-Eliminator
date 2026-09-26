import { useApi } from "../api";
import { Panel, StateBlock } from "../components/ui";
import { int, num } from "../format";

interface ImpactBlock {
  units: number;
  kg_assumed: number;
  co2e_kg: number;
  meals: number;
}
interface ImpactResp {
  holdout: [string, string];
  days: number;
  stores: number;
  waste_avoided: ImpactBlock;
  per_store_per_year: ImpactBlock;
  lost_sales: { model: number; naive: number };
  sources: {
    co2e_kg_per_kg: number;
    co2e_source: string;
    kg_per_meal: number;
    meals_source: string;
    hierarchy_source: string;
    assumed_kg_per_unit: Record<string, number>;
    caveat: string;
  };
  label: string;
  scope?: string;
  products_per_store?: number | null;
}

/** Social-good impact: waste avoided by forecast-driven ordering vs. "same as last week",
 *  converted with cited factors. Every assumption is printed next to the number it drives. */
export default function Impact() {
  const r = useApi<ImpactResp>("/api/impact");
  if (!r.data) {
    return (
      <div className="screen">
        <h1 className="display">Impact</h1>
        <Panel>
          <StateBlock loading={r.loading} error={r.error} onRetry={r.reload}
            notFound={<p><strong>Pipeline not run yet.</strong> Impact appears after <code>train_production</code>.</p>} />
        </Panel>
      </div>
    );
  }
  const d = r.data;
  const y = d.per_store_per_year;
  const lostCut = d.lost_sales.naive > 0 ? 1 - d.lost_sales.model / d.lost_sales.naive : null;
  return (
    <div className="screen">
      <header className="screen__head">
        <div>
          <h1 className="display">Impact</h1>
          <p className="screen__lede">
            Food that forecast-driven ordering keeps out of the bin, compared with ordering "same as last week" — same
            ordering rule, so the difference is forecast skill only.
          </p>
        </div>
      </header>

      <p className="honesty">
        <span className="honesty__tag">Read this first</span>
        {d.label} {d.scope} {d.sources.caveat}
      </p>

      <div className="grid-2">
        <Panel title="Per store, per year" sub={`Extrapolated from the ${d.days}-day holdout (${d.holdout[0]} → ${d.holdout[1]}), ${d.stores} stores, ~${d.products_per_store ?? "?"} sampled products each`}>
          <div className="impact">
            <Stat value={int(y.units)} label="units of waste avoided" />
            <Stat value={`${int(y.kg_assumed)} kg`} label="food kept out of the bin (assumed unit weights)" />
            <Stat value={`${num(y.co2e_kg / 1000, 1)} t`} label="CO₂e avoided" />
            <Stat value={int(y.meals)} label="meals' worth of food" />
          </div>
        </Panel>
        <Panel title="And fewer empty shelves" sub="Waste isn't cut by simply ordering less">
          <p className="impact__lead">
            Over the same holdout, forecast-driven ordering also lost{" "}
            <strong>{lostCut === null ? "—" : `${Math.round(lostCut * 100)}% fewer sales`}</strong> to stockouts than the
            naive policy ({int(d.lost_sales.model)} vs {int(d.lost_sales.naive)} units).
          </p>
          <p className="muted">
            Tomorrow's plan follows the EPA Wasted Food Scale: prevent waste by ordering right, mark down what's left, and
            donate what a markdown won't clear.
          </p>
        </Panel>
      </div>

      <Panel title="Where the numbers come from">
        <ul className="sources">
          <li>
            <strong>{d.sources.co2e_kg_per_kg} kg CO₂e per kg</strong> of wasted food — WRAP, UK household food waste
            2021-22 (≈16 Mt CO₂e for 6.0 Mt). <a href={d.sources.co2e_source} target="_blank" rel="noreferrer">source</a>
          </li>
          <li>
            <strong>{d.sources.kg_per_meal} kg per meal</strong> — ReFED: 29% of 240 M tons unsold or uneaten ≈ 114 B
            meals. <a href={d.sources.meals_source} target="_blank" rel="noreferrer">source</a>
          </li>
          <li>
            Surplus ladder — EPA Wasted Food Scale (prevent, then donate).{" "}
            <a href={d.sources.hierarchy_source} target="_blank" rel="noreferrer">source</a>
          </li>
          <li>
            <strong>Assumed</strong> average weight per sales unit:{" "}
            {Object.entries(d.sources.assumed_kg_per_unit).map(([c, w]) => `${c} ${w} kg`).join(" · ")}. Rohlik records
            sales in pieces or kg depending on the product.
          </li>
        </ul>
      </Panel>
    </div>
  );
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="impact__stat">
      <div className="impact__value">{value}</div>
      <div className="impact__label">{label}</div>
    </div>
  );
}
