import { describe, expect, it } from "vitest";

import { metaBatchStatusCopy } from "./meta";

describe("Meta Arena batch states", () => {
  it("explains that a waiting batch is gated only by normal portfolios with the same harness", () => {
    expect(metaBatchStatusCopy("waiting")).toContain("normal-portfolio run for the same harness");
  });

  it("identifies a ready batch as frozen and scoped to each meta portfolio", () => {
    expect(metaBatchStatusCopy("ready")).toContain(
      "matching its harness, managed/rebuilt mode, and long/short direction",
    );
  });

  it("explains why an insufficient batch cannot synthesize", () => {
    expect(metaBatchStatusCopy("insufficient")).toContain("enough usable");
  });

  it("explains that a failed packet blocks meta evaluations", () => {
    expect(metaBatchStatusCopy("failed")).toContain("not queued");
  });
});
