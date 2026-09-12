import { describe, expect, it } from "vitest";
import type { AllocationPolicy } from "./api/types";
import {
  decisionOutcomeLabel,
  referenceLabel,
  selectedTarget,
  sizeSelectedPositions,
  validateAllocation,
} from "./allocation";

const managed: AllocationPolicy = {
  min_position_weight_pct: 10,
  max_position_weight_pct: 25,
  derived_min_positions: 0,
  derived_max_positions: 10,
};
const rebuilt = { ...managed, max_position_weight_pct: 50 };
const positions = (weights: number[]) =>
  weights.map((weight_pct, index) => ({ symbol: ["AAPL", "MSFT", "SPY", "RSP"][index], weight_pct }));

describe("partial allocation entry", () => {
  it("sizes three capped managed selections at 75% and one rebuilt selection at 50%", () => {
    expect(sizeSelectedPositions(3, managed)).toEqual([25, 25, 25]);
    expect(sizeSelectedPositions(1, rebuilt)).toEqual([50]);
    expect(selectedTarget(3, 25)).toBe(75);
    expect(validateAllocation(positions([25, 25, 25]), "Only three qualify", false, managed)).toBeNull();
    expect(validateAllocation(positions([50]), "Only one qualifies", false, rebuilt)).toBeNull();
  });
  it("requires explicit, explained abstention and an explanation for partial selection", () => {
    expect(validateAllocation([], "No matches", true, managed)).toBeNull();
    expect(validateAllocation([], "", true, managed)).toMatch(/Explain/);
    expect(validateAllocation([], "No matches", false, managed)).toMatch(/choose No qualifying/);
    expect(validateAllocation(positions([25]), "", false, managed)).toMatch(/Explain/);
    expect(sizeSelectedPositions(0, managed)).toEqual([]);
  });
  it("supports full allocation and rejects discretionary underallocation or an excessive position", () => {
    expect(validateAllocation(positions([25, 25, 25, 25]), "", false, managed)).toBeNull();
    expect(validateAllocation(positions([20, 20, 20]), "Only three qualify", false, managed)).toMatch(
      /total 75/,
    );
    expect(validateAllocation(positions([30, 25, 20]), "Only three qualify", false, managed)).toMatch(
      /between/,
    );
  });
  it("rejects non-finite, nonpositive, duplicate, missing, and overprecise selections", () => {
    for (const weight of [Infinity, NaN, 0, -1])
      expect(validateAllocation(positions([weight]), "Reason", false, managed)).toMatch(/positive, finite/);
    expect(
      validateAllocation(
        [
          { symbol: "AAPL", weight_pct: 25 },
          { symbol: "aapl", weight_pct: 25 },
        ],
        "Reason",
        false,
        managed,
      ),
    ).toMatch(/only once/);
    expect(validateAllocation([{ symbol: "", weight_pct: 25 }], "Reason", false, managed)).toMatch(/symbol/);
    expect(validateAllocation(positions([24.99999]), "Reason", false, managed)).toMatch(/four decimal/);
  });
  it("distributes rounding without breaking the cap and rejects excessive counts", () => {
    expect(sizeSelectedPositions(3, { ...managed, max_position_weight_pct: 100 })).toEqual([
      33.3334, 33.3333, 33.3333,
    ]);
    expect(() => sizeSelectedPositions(11, managed)).toThrow(/Too many/);
    const narrow = {
      ...managed,
      min_position_weight_pct: 30,
      max_position_weight_pct: 31,
      derived_max_positions: 3,
    };
    expect(sizeSelectedPositions(3, narrow)).toEqual([31, 31, 31]);
  });
  it("identifies abstention as a decision and identifies direction-matched references", () => {
    expect(decisionOutcomeLabel("abstained")).toBe("Abstained");
    expect(decisionOutcomeLabel("partially_allocated")).toBe("Partially allocated");
    expect(referenceLabel("long")).toBe("SPY reference");
    expect(referenceLabel("short")).toBe("Short SPY reference");
  });
});
