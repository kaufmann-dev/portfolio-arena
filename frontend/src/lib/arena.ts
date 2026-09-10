import type { ArenaVersion, Direction, HorizonObjective } from "./api/types";

export const HORIZON_OBJECTIVES: { value: HorizonObjective; label: string }[] = [
  { value: "ci_lower", label: "Adjusted lower 95%" },
  { value: "information_ratio", label: "Information ratio" },
  { value: "sharpe", label: "Sharpe" },
  { value: "mean_daily_alpha", label: "Mean daily alpha" },
  { value: "hit_rate", label: "Hit rate" },
];

export function parseHorizonObjective(value: string | null): HorizonObjective {
  return HORIZON_OBJECTIVES.find((option) => option.value === value)?.value ?? "ci_lower";
}

export function horizonObjectiveLabel(value: HorizonObjective): string {
  return HORIZON_OBJECTIVES.find((option) => option.value === value)!.label;
}

export function parseDirection(value: string | null): Direction {
  return value === "short" ? "short" : "long";
}

export function selectedVersion(
  versions: ArenaVersion[],
  requested: string | null,
): ArenaVersion | undefined {
  return (
    versions.find((version) => String(version.id) === requested) ??
    [...versions].sort((a, b) => b.created_at.localeCompare(a.created_at) || b.id - a.id)[0]
  );
}

export function portfolioAnalysisHref(
  slug: string,
  track: "managed" | "rebuilt",
  direction: Direction,
  versionId: number,
  objective: HorizonObjective = "ci_lower",
): string {
  const query = new URLSearchParams({ track, direction, version: String(versionId) });
  if (track === "rebuilt") query.set("objective", objective);
  return `/p/${slug}?${query}`;
}
