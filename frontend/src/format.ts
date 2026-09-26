export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function pct(v: number | null | undefined, digits = 1): string {
  return isNum(v) ? `${(v * 100).toFixed(digits)}%` : "–";
}

/** Signed percent, e.g. +2.3% / −0.7% (true minus sign). */
export function spct(v: number | null | undefined, digits = 1): string {
  if (!isNum(v)) return "–";
  const s = (Math.abs(v) * 100).toFixed(digits);
  return `${v > 0 ? "+" : v < 0 ? "−" : "±"}${s}%`;
}

export function num(v: number | null | undefined, digits = 1): string {
  if (!isNum(v)) return "–";
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function int(v: number | null | undefined): string {
  return isNum(v) ? Math.round(v).toLocaleString("en-US") : "–";
}

export function signed(v: number | null | undefined, digits = 1): string {
  if (!isNum(v)) return "–";
  const s = Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${s}`;
}

export function wape(v: number | null | undefined): string {
  return isNum(v) ? (v * 100).toFixed(1) + "%" : "–";
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** "2024-06-03" → "Mon 3 Jun 2024" (parsed as a calendar date, no timezone shift). */
export function day(iso: string | null | undefined, opts: { year?: boolean; dow?: boolean } = {}): string {
  if (!iso) return "–";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  const parts = [
    opts.dow !== false ? DOW[d.getUTCDay()] : null,
    String(d.getUTCDate()),
    MONTHS[d.getUTCMonth()],
    opts.year !== false ? String(d.getUTCFullYear()) : null,
  ];
  return parts.filter(Boolean).join(" ");
}

export function shortDay(iso: string): string {
  return day(iso, { year: false, dow: false });
}

/** Timestamps from SQLite come back without an offset; show them as written. */
export function stamp(iso: string | null | undefined): string {
  if (!iso) return "–";
  const m = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(iso);
  return m ? `${m[1]} ${m[2]}` : iso;
}

// WMO weather interpretation codes (Open-Meteo).
export function weatherText(code: number | null | undefined): string {
  if (!isNum(code)) return "–";
  const c = Math.round(code);
  if (c === 0) return "Clear";
  if (c === 1) return "Mainly clear";
  if (c === 2) return "Partly cloudy";
  if (c === 3) return "Overcast";
  if (c === 45 || c === 48) return "Fog";
  if (c >= 51 && c <= 57) return "Drizzle";
  if (c >= 61 && c <= 67) return "Rain";
  if (c >= 71 && c <= 77) return "Snow";
  if (c >= 80 && c <= 82) return "Showers";
  if (c === 85 || c === 86) return "Snow showers";
  if (c >= 95) return "Thunderstorm";
  return `Code ${c}`;
}

export type WeatherKind = "sun" | "partly" | "cloud" | "fog" | "rain" | "snow" | "storm" | "unknown";
export function weatherKind(code: number | null | undefined): WeatherKind {
  if (!isNum(code)) return "unknown";
  const c = Math.round(code);
  if (c <= 1) return "sun";
  if (c === 2) return "partly";
  if (c === 3) return "cloud";
  if (c === 45 || c === 48) return "fog";
  if ((c >= 51 && c <= 67) || (c >= 80 && c <= 82)) return "rain";
  if ((c >= 71 && c <= 77) || c === 85 || c === 86) return "snow";
  if (c >= 95) return "storm";
  return "unknown";
}

export function humanize(key: string): string {
  return key
    .replace(/^weather_/, "weather: ")
    .replace(/_/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}
