export function pct(value: number | null | undefined, digits = 1): string {
  const scaled = rounded(value, digits, 100);
  if (scaled === null) return "—";
  const sign = scaled > 0 ? "+" : "";
  return `${sign}${scaled.toFixed(digits)}%`;
}

/** Percentage points that are already in % units (turnover, weights). */
export function pctPoints(value: number | null | undefined, digits = 1): string {
  const result = rounded(value, digits);
  return result === null ? "—" : `${result.toFixed(digits)}%`;
}

export function num(value: number | null | undefined, digits = 2): string {
  const result = rounded(value, digits);
  return result === null ? "—" : result.toFixed(digits);
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

function rounded(value: number | null | undefined, digits: number, scale = 1): number | null {
  if (value === null || value === undefined || !isFinite(value)) return null;
  const result = Number((value * scale).toFixed(digits));
  return Object.is(result, -0) ? 0 : result;
}

function roundedSignClass(value: number | null | undefined, digits: number, scale: number): string {
  const result = rounded(value, digits, scale);
  if (result === null || result === 0) return "";
  return result > 0 ? "pos" : "neg";
}

/** Sign color for decimal ratios rendered with pct(). */
export function pctSignClass(value: number | null | undefined, digits = 1): string {
  return roundedSignClass(value, digits, 100);
}

/** Sign color for percentage-point values rendered with pctPoints(). */
export function pctPointsSignClass(value: number | null | undefined, digits = 1): string {
  return roundedSignClass(value, digits, 1);
}
