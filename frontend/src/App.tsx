import { useEffect, useState } from "react";
import { useApi } from "./api";
import type { Meta, Store } from "./types";
import { ToastHost } from "./components/toast";
import Today from "./screens/Today";
import ModelHealth from "./screens/ModelHealth";
import LearningLoop from "./screens/LearningLoop";
import Ledger from "./screens/Ledger";

type Route = "today" | "health" | "loop" | "ledger";
const ROUTES: { id: Route; label: string; hint: string }[] = [
  { id: "today", label: "Today's plan", hint: "Orders, waste risk, markdowns" },
  { id: "health", label: "Model health", hint: "Accuracy, baselines, data sources" },
  { id: "loop", label: "Learning loop", hint: "Backtest replay, promotions" },
  { id: "ledger", label: "Ledger", hint: "Every prediction vs. outcome" },
];

function readRoute(): Route {
  const h = window.location.hash.replace(/^#\/?/, "") as Route;
  return ROUTES.some((r) => r.id === h) ? h : "today";
}

function readStore(): string | null {
  try {
    return window.localStorage.getItem("store");
  } catch {
    return null;
  }
}

export default function App() {
  const [route, setRoute] = useState<Route>(readRoute);
  const meta = useApi<Meta>("/api/meta");
  const stores = useApi<Store[]>("/api/stores");
  const [store, setStoreState] = useState<string | null>(readStore);

  useEffect(() => {
    const on = () => setRoute(readRoute());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  // Default to the first store once the list arrives (or if the remembered one is gone).
  useEffect(() => {
    const list = stores.data;
    if (list?.length && (!store || !list.some((s) => s.store_id === store))) setStoreState(list[0].store_id);
  }, [stores.data, store]);

  const setStore = (s: string) => {
    setStoreState(s);
    try {
      window.localStorage.setItem("store", s);
    } catch {
      /* storage unavailable */
    }
  };

  const name = meta.data?.app_display_name;
  useEffect(() => {
    const r = ROUTES.find((x) => x.id === route)?.label;
    document.title = name ? `${r} – ${name}` : r ?? "Demand planner";
  }, [name, route]);

  const storeList = stores.data ?? [];

  return (
    <ToastHost>
      <div className="shell">
        <aside className="rail">
          <div className="brand">
            <svg viewBox="0 0 32 32" className="brand__mark" aria-hidden>
              <path d="M7 23c0-9 7-15 18-15-1 10-7 16-15 16" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
              <path d="M12 19l6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
            <div>
              <div className="brand__name">{name ?? " "}</div>
              <div className="brand__tag">Perishable demand planning</div>
            </div>
          </div>
          <nav className="nav" aria-label="Screens">
            {ROUTES.map((r) => (
              <a key={r.id} href={`#/${r.id}`} className={`nav__item ${route === r.id ? "is-on" : ""}`} aria-current={route === r.id ? "page" : undefined}>
                <span className="nav__label">{r.label}</span>
                <span className="nav__hint">{r.hint}</span>
              </a>
            ))}
          </nav>
          <div className="rail__foot">
            <p>
              Forecasts are real model output on Rohlik sales data. Inventory and waste are simulated. Backtest screens are
              labeled as replays.
            </p>
          </div>
        </aside>

        <main className="main">
          {stores.error && (
            <div className="banner banner--error" role="alert">
              Couldn't load stores: {stores.error.message}{" "}
              <button className="btn btn--ghost" onClick={stores.reload}>
                Try again
              </button>
            </div>
          )}
          {route === "today" && <Today stores={storeList} store={store} setStore={setStore} />}
          {route === "health" && <ModelHealth stores={storeList} store={store} setStore={setStore} meta={meta.data} />}
          {route === "loop" && <LearningLoop meta={meta.data} />}
          {route === "ledger" && <Ledger stores={storeList} />}

          <footer className="footer">
            <span>{meta.data?.attribution ?? "Weather data by Open-Meteo.com (CC BY 4.0)."}</span>
            {name && <span className="footer__name">{name} · HackGT 13</span>}
          </footer>
        </main>
      </div>
    </ToastHost>
  );
}
