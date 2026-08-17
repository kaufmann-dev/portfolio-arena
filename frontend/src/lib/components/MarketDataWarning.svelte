<script lang="ts">
  import { onMount } from "svelte";

  import { apiJson } from "../api/client";
  import type { MarketDataSnapshot, MarketDataStatus } from "../api/types";
  import { marketDataWarning } from "../marketData";

  interface Props {
    status: MarketDataStatus;
    asOf: string | null;
    onReady?: () => void | Promise<void>;
  }

  const { status, asOf, onReady }: Props = $props();
  let liveStatus = $state<MarketDataStatus | null>(null);
  let targetAsOf = $state<string | null>(null);
  const displayedStatus = $derived(liveStatus ?? status);
  const warning = $derived(marketDataWarning(displayedStatus, asOf, targetAsOf));

  onMount(() => {
    if (status !== "updating") return;

    let stopped = false;
    let timer: number | undefined;

    async function poll(): Promise<void> {
      try {
        const snapshot = await apiJson<MarketDataSnapshot>("/api/market-data");
        if (stopped) return;
        liveStatus = snapshot.market_data_status;
        targetAsOf = snapshot.target_as_of;
        if (snapshot.market_data_status === "fresh") {
          stopped = true;
          if (onReady) await onReady();
          else window.location.reload();
          return;
        }
      } catch {
        // The visible snapshot remains usable; retry the tiny status request.
      }
      if (!stopped) timer = window.setTimeout(poll, 10_000);
    }

    void poll();
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  });
</script>

{#if warning}
  <div class="market-data-warning" role={warning.role}>
    <strong>{warning.title}</strong>
    <span>{warning.message}</span>
  </div>
{/if}

<style>
  .market-data-warning {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 11px 14px;
    border: 1px solid color-mix(in srgb, var(--warn) 70%, var(--border-strong));
    margin: 12px 0;
    color: var(--warn);
    background: var(--warn-bg);
    font-size: 13px;
  }

  .market-data-warning[role="status"] {
    border-color: color-mix(in srgb, var(--accent) 45%, var(--border-strong));
    color: var(--text);
    background: color-mix(in srgb, var(--accent) 7%, var(--surface));
  }

  strong {
    flex: 0 0 auto;
  }

  span {
    min-width: 0;
    overflow-wrap: anywhere;
  }

  @media (max-width: 640px) {
    .market-data-warning {
      align-items: flex-start;
      flex-direction: column;
      gap: 2px;
    }
  }
</style>
