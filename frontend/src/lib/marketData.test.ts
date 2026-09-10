import { describe, expect, it } from "vitest";

import type { Boundary } from "./api/types";
const close = (day: string): Boundary => ({ timestamp: `${day}T20:00:00Z`, phase: "close" });

import { combineMarketData, marketDataWarning } from "./marketData";

describe("combineMarketData", () => {
  it("distinguishes two prices on the same trading date", () => {
    const opening: Boundary = { timestamp: "2026-07-29T13:30:00Z", phase: "open" };
    expect(
      combineMarketData(
        { market_data_status: "fresh", as_of: close("2026-07-29") },
        { market_data_status: "fresh", as_of: opening },
      ).asOf,
    ).toEqual(opening);
  });
  it("keeps a stale leaderboard warning when comparison data is fresh", () => {
    expect(
      combineMarketData(
        { market_data_status: "stale", as_of: close("2026-07-28") },
        { market_data_status: "fresh", as_of: close("2026-07-29") },
      ),
    ).toEqual({ status: "stale", asOf: close("2026-07-28") });
  });

  it("keeps an unavailable leaderboard warning when comparison data is fresh", () => {
    expect(
      combineMarketData(
        { market_data_status: "unavailable", as_of: null },
        { market_data_status: "fresh", as_of: close("2026-07-29") },
      ),
    ).toEqual({ status: "unavailable", asOf: null });
  });

  it("keeps an updating snapshot visible when comparison data is fresh", () => {
    expect(
      combineMarketData(
        { market_data_status: "updating", as_of: close("2026-07-28") },
        { market_data_status: "fresh", as_of: close("2026-07-29") },
      ),
    ).toEqual({ status: "updating", asOf: close("2026-07-28") });
  });
});

describe("marketDataWarning", () => {
  it("does not warn for fresh data", () => {
    expect(marketDataWarning("fresh", close("2026-07-28"))).toBeNull();
  });

  it("does not warn for usable last-known data", () => {
    expect(marketDataWarning("stale", close("2026-07-28"))).toBeNull();
  });

  it("reports an in-progress close without calling usable data stale", () => {
    const warning = marketDataWarning("updating", close("2026-07-28"), close("2026-07-29"));

    expect(warning?.role).toBe("status");
    expect(warning?.title).toBe("Updating 2026-07-29 close");
    expect(warning?.message).toContain("complete 2026-07-28 close snapshot");
  });

  it("warns that unavailable prices make valuations incomplete", () => {
    const warning = marketDataWarning("unavailable", null);

    expect(warning?.role).toBe("alert");
    expect(warning?.title).toContain("incomplete");
    expect(warning?.message).toContain("cannot be displayed");
  });
});
