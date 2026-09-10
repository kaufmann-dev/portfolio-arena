import type { ArenaVersion, Direction } from "./api/types";

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
): string {
  const query = new URLSearchParams({ track, direction, version: String(versionId) });
  return `/p/${slug}?${query}`;
}
