// Chart tokens mirror the CSS custom properties in styles.css (Recharts needs literal values).
export const C = {
  ink: "#1F2937",
  ink2: "#4B5563",
  ink3: "#6B7280",
  grid: "#E5E7EB",
  axis: "#D1D5DB",
  surface: "#FFFFFF",
  spruce: "#15803D",
  spruceMid: "#22C55E",
  spruceLight: "#86EFAC",
  neutralBar: "#9CA3AF",
  good: "#15803D",
  critical: "#DC2626",
  warning: "#D97706",
  bandA: "#F0FDF4",
  bandB: "#FFFFFF",
  forecast: "#2563EB",
};

/** Fixed order, names and colours for the five product categories. */
export const CATEGORIES: { key: string; label: string; color: string }[] = [
  { key: "Bakery", label: "Bakery", color: "#EF4444" },
  { key: "Fruit and vegetable", label: "Fruits and vegetables", color: "#F97316" },
  { key: "Meat and fish", label: "Meat and fish", color: "#EAB308" },
  { key: "Dairy products", label: "Dairy products", color: "#22C55E" },
  { key: "Eggs", label: "Eggs", color: "#3B82F6" },
];
export const categoryLabel = (key: string) => CATEGORIES.find((c) => c.key === key)?.label ?? key;
export const categoryColor = (key: string) => CATEGORIES.find((c) => c.key === key)?.color ?? "#9CA3AF";

export const axisProps = {
  stroke: C.axis,
  tick: { fill: C.ink3, fontSize: 11 },
  tickLine: false,
} as const;
