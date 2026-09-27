import { useCallback, useEffect, useState } from "react";

/** Write token for a locked deployment: open the dashboard once with ?token=… and it is kept locally. */
/** Save the store access code (the server's API_TOKEN) in this browser. */
export function setAccessCode(code: string): void {
  try {
    localStorage.setItem("apiToken", code.trim());
  } catch {
    /* storage unavailable: the code lasts for this page only */
    memoryCode = code.trim();
  }
}
let memoryCode = "";

function authHeaders(): Record<string, string> {
  try {
    const url = new URL(window.location.href);
    const q = url.searchParams.get("token");
    if (q) {
      localStorage.setItem("apiToken", q);
      url.searchParams.delete("token"); // don't leave the code in the address bar or history
      window.history.replaceState(null, "", url.toString());
    }
    const t = localStorage.getItem("apiToken") || memoryCode;
    return t ? { Authorization: `Bearer ${t}` } : {};
  } catch {
    return {};
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function parseError(res: Response): Promise<ApiError> {
  let msg = res.statusText || `HTTP ${res.status}`;
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") msg = body.detail;
    else if (Array.isArray(body?.detail)) msg = body.detail.map((d: { msg?: string }) => d.msg).join("; ");
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(res.status, msg);
}

export async function getJSON<T>(url: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { signal, headers: { Accept: "application/json" } });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "Can't reach the API. Is the backend running on port 8000?");
  }
  if (!res.ok) throw await parseError(res);
  return res.json() as Promise<T>;
}

export async function postFile<T>(url: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  let res: Response;
  try {
    res = await fetch(url, { method: "POST", body: fd, headers: authHeaders() });
  } catch {
    throw new ApiError(0, "Can't reach the API. Is the backend running on port 8000?");
  }
  if (!res.ok) throw await parseError(res);
  return res.json() as Promise<T>;
}

export interface ApiState<T> {
  data: T | undefined;
  error: ApiError | undefined;
  loading: boolean;
  reload: () => void;
}

/** Fetch `url` (null = don't fetch). Keeps the previous data while a new request is in flight,
 * so tables don't blank out when a parameter (e.g. the waste-cost slider) changes. */
export function useApi<T>(url: string | null): ApiState<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError>();
  const [loading, setLoading] = useState(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!url) return;
    const ctrl = new AbortController();
    setLoading(true);
    setError(undefined);
    getJSON<T>(url, ctrl.signal)
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch((e: Error) => {
        if (e.name === "AbortError") return;
        setError(e instanceof ApiError ? e : new ApiError(0, e.message));
        setData(undefined);
        setLoading(false);
      });
    return () => ctrl.abort();
  }, [url, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  return { data, error, loading, reload };
}

export function qs(params: Record<string, string | number | undefined | null>): string {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  const s = u.toString();
  return s ? `?${s}` : "";
}

export async function postJSON<T>(url: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", ...authHeaders() }, body: JSON.stringify(body) });
  } catch {
    throw new ApiError(0, "Can't reach the API. Is the backend running on port 8000?");
  }
  if (!res.ok) throw await parseError(res);
  return res.json() as Promise<T>;
}
