import { describe, expect, it } from "vitest";

import { num, pct, pctPoints, pctPointsSignClass, pctSignClass } from "./format";

describe("numeric display normalization", () => {
  it("renders exact and rounded ratio zeros without a sign or color", () => {
    expect(pct(-0, 1)).toBe("0.0%");
    expect(pct(-0.0004, 1)).toBe("0.0%");
    expect(pctSignClass(-0.0004, 1)).toBe("");
  });

  it("keeps ratio signs when they survive the displayed precision", () => {
    expect(pct(-0.0006, 1)).toBe("-0.1%");
    expect(pctSignClass(-0.0006, 1)).toBe("neg");
    expect(pct(0.0006, 1)).toBe("+0.1%");
    expect(pctSignClass(0.0006, 1)).toBe("pos");
  });

  it("renders rounded percentage-point zeros neutrally", () => {
    expect(pctPoints(-0.04, 1)).toBe("0.0%");
    expect(pctPointsSignClass(-0.04, 1)).toBe("");
    expect(pctPoints(-0.06, 1)).toBe("-0.1%");
    expect(pctPointsSignClass(-0.06, 1)).toBe("neg");
  });

  it("normalizes negative zero in plain numbers", () => {
    expect(num(-Number.EPSILON, 2)).toBe("0.00");
  });
});
