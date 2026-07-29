import type { MarketDataStatus } from "./api/types";

export interface MarketDataSource {
  as_of: string | null;
  market_data_status: MarketDataStatus;
}

export interface CombinedMarketData {
  status: MarketDataStatus;
  asOf: string | null;
}

export interface MarketDataWarningContent {
  title: string;
  message: string;
  role: "status" | "alert";
}

const MARKET_DATA_SEVERITY: Record<MarketDataStatus, number> = {
  fresh: 0,
  stale: 1,
  unavailable: 2,
};

export function combineMarketData(...sources: (MarketDataSource | null | undefined)[]): CombinedMarketData {
  let status: MarketDataStatus = "fresh";
  let asOf: string | null | undefined;

  for (const source of sources) {
    if (!source) continue;

    if (MARKET_DATA_SEVERITY[source.market_data_status] > MARKET_DATA_SEVERITY[status]) {
      status = source.market_data_status;
    }

    if (asOf === undefined) {
      asOf = source.as_of;
    } else if (asOf !== null && source.as_of === null) {
      asOf = null;
    } else if (asOf !== null && source.as_of !== null && source.as_of < asOf) {
      asOf = source.as_of;
    }
  }

  return { status, asOf: asOf ?? null };
}

export function marketDataWarning(
  status: MarketDataStatus,
  asOf: string | null,
): MarketDataWarningContent | null {
  if (status === "fresh") return null;

  if (status === "stale") {
    return {
      title: "Market data refresh delayed",
      message: asOf
        ? `Showing last-known prices valued through the ${asOf} close.`
        : "Showing last-known prices while the latest market data refresh is retried.",
      role: "status",
    };
  }

  return {
    title: "Market data incomplete",
    message: asOf
      ? `Some required prices are unavailable. Displayed valuations use data through the ${asOf} close and may be incomplete.`
      : "Required prices are unavailable, so a complete valuation cannot be displayed.",
    role: "alert",
  };
}
