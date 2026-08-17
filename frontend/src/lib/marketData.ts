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
  updating: 1,
  stale: 2,
  unavailable: 3,
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
  targetAsOf: string | null = null,
): MarketDataWarningContent | null {
  if (status === "updating") {
    return {
      title: targetAsOf ? `Updating ${targetAsOf} close` : "Updating latest close",
      message: asOf
        ? `Valuations remain on the complete ${asOf} snapshot and will refresh automatically.`
        : "Valuations will appear automatically as soon as the complete snapshot is ready.",
      role: "status",
    };
  }
  if (status !== "unavailable") return null;

  return {
    title: "Market data incomplete",
    message: asOf
      ? `Some required prices are unavailable. Displayed valuations use data through the ${asOf} close and may be incomplete.`
      : "Required prices are unavailable, so a complete valuation cannot be displayed.",
    role: "alert",
  };
}
