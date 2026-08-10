import { describe, expect, it } from "vitest";

import {
  DEFAULT_REBUILT_VIEW,
  parseDirection,
  portfolioAnalysisHref,
  rebuiltContext,
  rebuiltContextParams,
} from "./arena";

describe("arena direction", () => {
  it("defaults invalid or absent values to long", () => {
    expect(parseDirection(null)).toBe("long");
    expect(parseDirection("sideways")).toBe("long");
  });

  it("accepts the short direction", () => {
    expect(parseDirection("short")).toBe("short");
  });

  it("carries direction into managed detail links", () => {
    expect(portfolioAnalysisHref("example", "managed", "short")).toBe(
      "/p/example?track=managed&direction=short",
    );
  });
});

describe("rebuilt analysis context", () => {
  it("defaults rebuilt overviews to a net portfolio-tuned policy", () => {
    expect(rebuiltContext(DEFAULT_REBUILT_VIEW, "canonical", "net", 5)).toEqual({
      view: "tuned",
      objective: "canonical",
      cost_basis: "net",
      horizon: null,
    });
  });

  it("omits a horizon from constructed-policy views", () => {
    const context = rebuiltContext("common", "max_alpha", "net", 17);

    expect(context).toEqual({
      view: "common",
      objective: "max_alpha",
      cost_basis: "net",
      horizon: null,
    });
    expect(rebuiltContextParams(context).has("horizon")).toBe(false);
  });

  it("forces direct Signal Alpha onto its valid gross canonical contract", () => {
    const context = rebuiltContext("signal", "max_sharpe", "net", 7);

    expect(context).toEqual({
      view: "signal",
      objective: "canonical",
      cost_basis: "gross",
      horizon: 7,
    });
  });

  it("bounds Signal Alpha horizons", () => {
    expect(rebuiltContext("signal", "canonical", "gross", 0).horizon).toBe(1);
    expect(rebuiltContext("signal", "canonical", "gross", 99).horizon).toBe(20);
  });

  it("carries the complete context into rebuilt detail links", () => {
    const context = rebuiltContext("signal", "canonical", "gross", 12);
    const href = portfolioAnalysisHref("example", "rebuilt", "short", context);
    const url = new URL(href, "https://arena.test");

    expect(url.pathname).toBe("/p/example");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      view: "signal",
      objective: "canonical",
      cost_basis: "gross",
      track: "rebuilt",
      horizon: "12",
      direction: "short",
    });
  });
});
