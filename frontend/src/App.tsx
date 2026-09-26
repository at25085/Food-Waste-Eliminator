import { useEffect, useState } from "react";
import { useApi } from "./api";
import type { Meta, Store } from "./types";
import { ToastHost } from "./components/toast";
import Today from "./screens/Today";
import Ledger from "./screens/Ledger";
import StoreData from "./screens/StoreData";
import Overview from "./screens/Overview";
import About from "./screens/About";
import ChatWidget from "./components/ChatWidget";

type Route = "overview" | "today" | "data" | "ledger" | "about";
const ROUTES: { id: Route; label: string; hint: string }[] = [
  { id: "overview", label: "Dashboard", hint: "Tomorrow at a glance" },
  { id: "today", label: "Today's plan", hint: "Orders, waste risk, markdowns" },
  { id: "data", label: "Upload", hint: "Add a store, upload daily sheets" },
  { id: "ledger", label: "Ledger", hint: "Every prediction vs. outcome" },
  { id: "about", label: "About", hint: "Why this exists and how it works" },
];

const CHAIN_ORDER = ["Atlanta", "Macon", "Augusta"];

/** The chain's stores first, in the chain's order, then stores owners add. The training
 *  warehouses stay in the database (the model learns from them) but aren't shown. */
function visibleStores(all: Store[] | undefined): Store[] {
  const rank = (s: Store) => (CHAIN_ORDER.includes(s.store_id) ? CHAIN_ORDER.indexOf(s.store_id) : CHAIN_ORDER.length);
  return (all ?? [])
    .filter((s) => s.kind === "demo" || s.kind === "owner")
    .sort((a, b) => rank(a) - rank(b) || a.store_id.localeCompare(b.store_id));
}

function readRoute(): Route {
  const h = window.location.hash.replace(/^#\/?/, "") as Route;
  return ROUTES.some((r) => r.id === h) ? h : "overview";
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
    const list = visibleStores(stores.data);
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
    document.title = name ? `${r} – ${name}` : r ?? "Freshora";
  }, [name, route]);

  const storeList = visibleStores(stores.data);

  return (
    <ToastHost>
      <div className="shell">
        <header className="topbar">
          <div className="topbar__in">
            <a className="brand" href="#/overview">
              <svg viewBox="0 0 32 32" className="brand__mark" aria-hidden>
                <path d="M7 23c0-9 7-15 18-15-1 10-7 16-15 16" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
                <path d="M12 19l6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
              </svg>
              <div>
                <div className="brand__name">{name ?? " "}</div>
              </div>
            </a>
            <nav className="nav" aria-label="Screens">
              {ROUTES.map((r) => (
                <a key={r.id} href={`#/${r.id}`} title={r.hint} className={`nav__item ${route === r.id ? "is-on" : ""}`} aria-current={route === r.id ? "page" : undefined}>
                  {r.label}
                </a>
              ))}
            </nav>
          </div>
        </header>

        <main className="main">
          {stores.error && (
            <div className="banner banner--error" role="alert">
              Couldn't load stores: {stores.error.message}{" "}
              <button className="btn btn--ghost" onClick={stores.reload}>
                Try again
              </button>
            </div>
          )}
          {route === "overview" && <Overview stores={storeList} store={store} setStore={setStore} />}
          {route === "today" && <Today stores={storeList} store={store} setStore={setStore} />}
          {route === "data" && <StoreData stores={storeList} store={store} setStore={setStore} reloadStores={stores.reload} />}
          {route === "ledger" && <Ledger stores={storeList} store={store} />}
          {route === "about" && <About name={name} />}
        </main>
        <ChatWidget store={store} storeName={storeList.find((s) => s.store_id === store)?.city ?? null} />
      </div>
    </ToastHost>
  );
}
