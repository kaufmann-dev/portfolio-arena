import { describe, expect, it } from "vitest";

import { fmtDate, num, pct, pctPoints, pctPointsSignClass, pctSignClass } from "./format";

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

describe("market boundary labels", () => {
  it("distinguishes opening and closing marks on the same day", () => {
    expect(fmtDate({ timestamp: "2026-09-01T13:30:00+00:00", phase: "open" })).toBe("2026-09-01 open");
    expect(fmtDate({ timestamp: "2026-09-01T20:00:00+00:00", phase: "close" })).toBe("2026-09-01 close");
  });
});
