<script lang="ts">
  import type { MarketDataStatus } from "../api/types";
  import { marketDataWarning } from "../marketData";

  interface Props {
    status: MarketDataStatus;
    asOf: string | null;
  }

  const { status, asOf }: Props = $props();
  const warning = $derived(marketDataWarning(status, asOf));
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
