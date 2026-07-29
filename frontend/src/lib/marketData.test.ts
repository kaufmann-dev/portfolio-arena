import { describe, expect, it } from "vitest";

import { combineMarketData, marketDataWarning } from "./marketData";

describe("combineMarketData", () => {
  it("keeps a stale leaderboard warning when comparison data is fresh", () => {
    expect(
      combineMarketData(
        { market_data_status: "stale", as_of: "2026-07-28" },
        { market_data_status: "fresh", as_of: "2026-07-29" },
      ),
    ).toEqual({ status: "stale", asOf: "2026-07-28" });
  });

  it("keeps an unavailable leaderboard warning when comparison data is fresh", () => {
    expect(
      combineMarketData(
        { market_data_status: "unavailable", as_of: null },
        { market_data_status: "fresh", as_of: "2026-07-29" },
      ),
    ).toEqual({ status: "unavailable", asOf: null });
  });
});

describe("marketDataWarning", () => {
  it("does not warn for fresh data", () => {
    expect(marketDataWarning("fresh", "2026-07-28")).toBeNull();
  });

  it("labels stale prices as last-known and keeps the valued close", () => {
    const warning = marketDataWarning("stale", "2026-07-28");

    expect(warning?.role).toBe("status");
    expect(warning?.message).toContain("last-known");
    expect(warning?.message).toContain("2026-07-28 close");
  });

  it("warns that unavailable prices make valuations incomplete", () => {
    const warning = marketDataWarning("unavailable", null);

    expect(warning?.role).toBe("alert");
    expect(warning?.title).toContain("incomplete");
    expect(warning?.message).toContain("cannot be displayed");
  });
});
