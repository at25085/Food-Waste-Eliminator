// Chart tokens mirror the CSS custom properties in styles.css (Recharts needs literal values).
export const C = {
  ink: "#15302B",
  ink2: "#4B605B",
  ink3: "#7A8C87",
  grid: "#E3E9E6",
  axis: "#C5D0CB",
  surface: "#FFFFFF",
  spruce: "#1F5A4E",
  spruceMid: "#4F8C7E",
  spruceLight: "#A7C9C0",
  neutralBar: "#B4C0BC",
  good: "#2E7D46",
  critical: "#C8412F",
  warning: "#B77400",
  bandA: "#EEF4F1",
  bandB: "#FFFFFF",
};

export const axisProps = {
  stroke: C.axis,
  tick: { fill: C.ink3, fontSize: 11 },
  tickLine: false,
} as const;
