export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !isFinite(value)) return "—";
  const scaled = value * 100;
  const sign = scaled > 0 ? "+" : "";
  return `${sign}${scaled.toFixed(digits)}%`;
}

/** Percentage points that are already in % units (turnover, weights). */
export function pctPoints(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !isFinite(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !isFinite(value)) return "—";
  return value.toFixed(digits);
}

export function fmtDate(value: string | null | undefined): string {
  if (!value) return "—";
  return value;
}

export function fmtDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (isNaN(parsed.getTime())) return value;
  return parsed.toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

export function ageLabel(days: number | null | undefined): string {
  if (days === null || days === undefined) return "—";
  if (days < 60) return `${days}d`;
  if (days < 730) return `${Math.floor(days / 30.44)}mo`;
  return `${(days / 365.25).toFixed(1)}y`;
}

export function signClass(value: number | null | undefined): string {
  if (value === null || value === undefined || !isFinite(value) || value === 0) return "";
  return value > 0 ? "pos" : "neg";
}
