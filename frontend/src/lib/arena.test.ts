import { describe, expect, it } from "vitest";
import {
  HORIZON_OBJECTIVES,
  parseDirection,
  parseHorizonObjective,
  portfolioAnalysisHref,
  selectedVersion,
} from "./arena";
const versions = [
  { id: 1, name: "v1", evaluation_enabled: false, created_at: "2026-07-01T00:00:00Z" },
  { id: 2, name: "v2", evaluation_enabled: true, created_at: "2026-08-01T00:00:00Z" },
];
describe("Arena version navigation", () => {
  it("selects the newest version regardless of evaluation state", () => {
    expect(
      selectedVersion([...versions, { ...versions[1], id: 3, evaluation_enabled: false }], null)?.id,
    ).toBe(3);
  });
  it("keeps explicitly selected old versions visible", () => {
    expect(selectedVersion(versions, "1")?.id).toBe(1);
    expect(selectedVersion(versions, "missing")?.id).toBe(2);
    expect(selectedVersion([], null)).toBeUndefined();
  });
  it("preserves track, direction and version in portfolio links", () => {
    expect(portfolioAnalysisHref("sample", "rebuilt", "short", 1)).toBe(
      "/p/sample?track=rebuilt&direction=short&version=1&objective=signal_mean_daily_alpha",
    );
  });
  it("preserves the optimization objective only for rebuilt portfolio links", () => {
    expect(portfolioAnalysisHref("sample", "rebuilt", "long", 2, "sharpe")).toBe(
      "/p/sample?track=rebuilt&direction=long&version=2&objective=sharpe",
    );
    expect(portfolioAnalysisHref("sample", "managed", "long", 2, "sharpe")).toBe(
      "/p/sample?track=managed&direction=long&version=2",
    );
  });
  it("accepts all six objectives and defaults invalid URLs to signal alpha", () => {
    for (const { value } of HORIZON_OBJECTIVES) expect(parseHorizonObjective(value)).toBe(value);
    expect(parseHorizonObjective(null)).toBe("signal_mean_daily_alpha");
    expect(parseHorizonObjective("unknown")).toBe("signal_mean_daily_alpha");
  });
  it("defaults unknown directions to long", () => {
    expect(parseDirection("short")).toBe("short");
    expect(parseDirection(null)).toBe("long");
  });
});
