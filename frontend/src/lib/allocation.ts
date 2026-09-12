import type { AllocationPolicy, DecisionOutcome, Direction, PositionOut } from "./api/types";

const PRECISION = 10_000;

export function decisionOutcomeLabel(outcome: DecisionOutcome): string {
  return { allocated: "Allocated", partially_allocated: "Partially allocated", abstained: "Abstained" }[
    outcome
  ];
}

export function referenceLabel(direction: Direction): string {
  return `${direction === "short" ? "Short SPY" : "SPY"} reference`;
}

export function selectedTarget(count: number, maximum = 100): number {
  return Math.min(100, Math.round(count * maximum * PRECISION) / PRECISION);
}

/** Equal sizing respects both limits and distributes four-decimal rounding across positions. */
export function sizeSelectedPositions(count: number, policy?: AllocationPolicy): number[] {
  if (!count) return [];
  const total = Math.round(selectedTarget(count, policy?.max_position_weight_pct) * PRECISION);
  const base = Math.floor(total / count);
  const residue = total - base * count;
  const weights = Array.from({ length: count }, (_, index) => (base + (index < residue ? 1 : 0)) / PRECISION);
  if (
    policy &&
    weights.some(
      (weight) => weight < policy.min_position_weight_pct || weight > policy.max_position_weight_pct,
    )
  ) {
    throw new Error("Too many positions for the minimum weight. Remove a position before sizing.");
  }
  return weights;
}

export function validateAllocation(
  positions: PositionOut[],
  note: string,
  abstained: boolean,
  policy?: AllocationPolicy,
): string | null {
  if (!positions.length && !abstained) return "Enter a position or choose No qualifying securities.";
  if (positions.some((position) => !position.symbol.trim())) return "Enter a symbol for every position.";
  const symbols = positions.map((position) => position.symbol.trim().toUpperCase());
  if (new Set(symbols).size !== symbols.length) return "Each symbol can appear only once.";
  if (positions.some((position) => !Number.isFinite(position.weight_pct) || position.weight_pct <= 0))
    return "Every selected position needs a positive, finite weight.";
  if (
    positions.some(
      (position) =>
        Math.abs(position.weight_pct * PRECISION - Math.round(position.weight_pct * PRECISION)) > 1e-6,
    )
  )
    return "Use no more than four decimal places for weights.";
  if (
    policy &&
    positions.some(
      (position) =>
        position.weight_pct < policy.min_position_weight_pct ||
        position.weight_pct > policy.max_position_weight_pct,
    )
  )
    return `Every position must be between ${policy.min_position_weight_pct}% and ${policy.max_position_weight_pct}%.`;
  const target = selectedTarget(positions.length, policy?.max_position_weight_pct);
  const total = positions.reduce((sum, position) => sum + Math.round(position.weight_pct * PRECISION), 0);
  if (total !== Math.round(target * PRECISION))
    return `Selected weights must total ${target}%; the remainder is allocated to the reference.`;
  if (target < 100 && !note.trim()) return "Explain why fewer or no securities qualify.";
  return null;
}
