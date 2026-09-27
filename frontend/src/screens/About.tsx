import { useApi } from "../api";
import { int, num, pct } from "../format";

interface ImpactResp {
  per_store_per_year: { units: number; kg_assumed: number; co2e_kg: number; meals: number };
  lost_sales: { model: number; naive: number };
  label: string;
}
interface Headline {
  headline?: { model_wape: number; last_week_wape: number; relative_to_last_week: number; holdout: [string, string] };
}

const STATS = [
  {
    value: "1.05B",
    text: "tonnes of food wasted in 2022 by households, food service and retail.",
    source: "UNEP Food Waste Index Report 2024",
    href: "https://www.unep.org/resources/publication/food-waste-index-report-2024",
    tone: "red",
  },
  {
    value: "8–10%",
    text: "of global greenhouse-gas emissions come from food that is lost or wasted.",
    source: "UNEP Food Waste Index Report 2024",
    href: "https://www.unep.org/resources/publication/food-waste-index-report-2024",
    tone: "orange",
  },
  {
    value: "≈ 1/3",
    text: "of the food produced for people is lost or wasted along the way.",
    source: "FAO, Global food losses and food waste (2011)",
    href: "https://www.fao.org/4/mb060e/mb060e00.htm",
    tone: "yellow",
  },
];

const STEPS = [
  {
    title: "Upload data",
    text: "Drop in the store's daily sheet (received, sold, wasted, price, discount) and, optionally, a batch sheet with delivery and expiry dates.",
  },
  {
    title: "Forecast",
    text: "One XGBoost model, trained on every store's history, predicts tomorrow's sales per product from recent sales, prices, discounts, holidays and weather.",
  },
  {
    title: "Plan",
    text: "Orders net of stock still on the shelf, a waste-risk flag, the markdown that keeps the most money, and what to donate. Download it as a CSV.",
  },
  {
    title: "Learn",
    text: "The next upload grades yesterday's forecasts. A retrained model only replaces the current one if it is better for this store and no worse for the others.",
  },
];

/** The team's About page, with sourced statistics and the system's own measured numbers. */
export default function About({ name }: { name?: string }) {
  const impact = useApi<ImpactResp>("/api/impact");
  const h = useApi<Headline>("/api/production/report").data?.headline;
  const y = impact.data?.per_store_per_year;
  const lost = impact.data?.lost_sales;
  const lostCut = lost && lost.naive > 0 ? 1 - lost.model / lost.naive : null;

  return (
    <div className="screen about">
      <div className="panel about__intro">
        <h1 className="display">About {name ?? "us"}</h1>
        <p className="screen__lede">Forecasting fresh-food demand so less of it ends up in the bin.</p>
      </div>

      <section className="about__mission slide-up">
        <h2>Our mission</h2>
        <p>
          Give independent grocers the forecasting that big chains pay for: order what will sell, mark down only when it pays,
          and donate what's left before it spoils.
        </p>
      </section>

      <div className="about__stats">
        {STATS.map((s) => (
          <div key={s.value} className={`about__stat about__stat--${s.tone} slide-up`}>
            <div className="about__statv">{s.value}</div>
            <p>{s.text}</p>
            <a href={s.href} target="_blank" rel="noreferrer">
              {s.source}
            </a>
          </div>
        ))}
      </div>

      <div className="grid-2">
        <section className="panel about__card slide-up">
          <h3>The problem</h3>
          <p>
            Most small grocers order fresh food by habit, often "about the same as last week". Perishables expire in days, so a
            wrong guess means empty shelves one day and food in the bin the next. Food that rots in landfill releases methane,
            and everything used to grow, move and chill it is wasted with it.
          </p>
        </section>
        <section className="panel about__card slide-up">
          <h3>What we do about it</h3>
          <p>
            We forecast tomorrow's sales for every fresh product and turn the forecast into decisions: how much to order, which
            stock is at risk, the discount that keeps the most money, and what to donate. Every forecast is checked against what
            actually sold, and the model only changes when a new version proves it's better.
          </p>
        </section>
      </div>

      <section className="about__impact slide-up">
        <h2>Measured, not promised</h2>
        <div className="about__impactgrid">
          <div>
            <div className="about__impactv">{h ? `${Math.round(h.relative_to_last_week * 100)}%` : "—"}</div>
            <p>
              less forecast error than "same as last week" on four weeks the model never saw
              {h ? ` (${pct(h.model_wape)} vs ${pct(h.last_week_wape)} error)` : ""}.
            </p>
          </div>
          <div>
            <div className="about__impactv">{y ? `${int(y.kg_assumed)} kg` : "—"}</div>
            <p>
              of food kept out of the bin per store per year{y ? `, ≈ ${int(y.meals)} meals and ${num(y.co2e_kg / 1000, 1)} t CO₂e` : ""}.
            </p>
          </div>
          <div>
            <div className="about__impactv">{lostCut == null ? "—" : `${Math.round(lostCut * 100)}%`}</div>
            <p>fewer lost sales to empty shelves at the same time, so waste isn't cut by simply ordering less.</p>
          </div>
        </div>
        <p className="about__caveat">
          Measured on four weeks of real grocery sales the model never saw. Waste figures come from a shelf-life simulator (no
          public dataset records waste) and use assumed unit weights.
        </p>
      </section>

      <section className="panel about__card">
        <h3 className="about__center">How it works</h3>
        <ol className="about__steps">
          {STEPS.map((s, k) => (
            <li key={s.title} className="slide-up" style={{ animationDelay: `${0.1 * k}s` }}>
              <span className="about__stepn">{k + 1}</span>
              <h4>{s.title}</h4>
              <p>{s.text}</p>
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
