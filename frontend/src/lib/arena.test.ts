import { describe, expect, it } from "vitest";
import { parseDirection, portfolioAnalysisHref, selectedVersion } from "./arena";
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
      "/p/sample?track=rebuilt&direction=short&version=1",
    );
  });
  it("defaults unknown directions to long", () => {
    expect(parseDirection("short")).toBe("short");
    expect(parseDirection(null)).toBe("long");
  });
});
