import type {
  CostBasis,
  Direction,
  RebuiltAnalysisContext,
  RebuiltObjective,
  RebuiltView,
} from "./api/types";

export function parseDirection(value: string | null): Direction {
  return value === "short" ? "short" : "long";
}

export function rebuiltContext(
  view: RebuiltView,
  objective: RebuiltObjective,
  costBasis: CostBasis,
  horizon: number,
): RebuiltAnalysisContext {
  if (view === "signal") {
    return {
      view,
      objective: "canonical",
      cost_basis: "gross",
      horizon: Math.min(20, Math.max(1, Math.trunc(horizon))),
    };
  }
  return { view, objective, cost_basis: costBasis, horizon: null };
}

export function rebuiltContextParams(context: RebuiltAnalysisContext, includeTrack = false): URLSearchParams {
  const query = new URLSearchParams({
    view: context.view,
    objective: context.objective,
    cost_basis: context.cost_basis,
  });
  if (includeTrack) query.set("track", "rebuilt");
  if (context.horizon !== null) query.set("horizon", String(context.horizon));
  return query;
}

export function portfolioAnalysisHref(
  slug: string,
  track: "managed" | "rebuilt",
  direction: Direction,
  context?: RebuiltAnalysisContext,
): string {
  if (track === "managed") return `/p/${slug}?track=managed&direction=${direction}`;
  const query = rebuiltContextParams(context ?? rebuiltContext("common", "canonical", "net", 1), true);
  query.set("direction", direction);
  return `/p/${slug}?${query.toString()}`;
}
