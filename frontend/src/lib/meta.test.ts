import { describe, expect, it } from "vitest";

import { metaBatchStatusCopy } from "./meta";

describe("Meta Arena batch states", () => {
  it("explains that a waiting batch is gated by normal portfolios", () => {
    expect(metaBatchStatusCopy("waiting")).toContain("normal-portfolio run");
  });

  it("identifies a ready batch as frozen and shared", () => {
    expect(metaBatchStatusCopy("ready")).toContain("exact snapshot");
  });

  it("explains why an insufficient batch cannot synthesize", () => {
    expect(metaBatchStatusCopy("insufficient")).toContain("enough usable");
  });

  it("explains that a failed packet blocks meta evaluations", () => {
    expect(metaBatchStatusCopy("failed")).toContain("not queued");
  });
});
