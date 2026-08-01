<script lang="ts">
  import type { RebuiltAnalysisContext, RebuiltArenaPortfolio, SignalHorizon } from "../api/types";
  import { portfolioAnalysisHref } from "../arena";
  import { pct } from "../format";
  import { link } from "../stores/router.svelte";

  interface Props {
    rows: RebuiltArenaPortfolio[];
    selectedHorizon: number;
    context: RebuiltAnalysisContext;
    benchmarkName: string;
  }

  const { rows, selectedHorizon, context, benchmarkName }: Props = $props();
  const horizons = Array.from({ length: 20 }, (_, index) => index + 1);

  function cellFor(row: RebuiltArenaPortfolio, horizon: number): SignalHorizon | undefined {
    return row.signal_horizons.find((cell) => cell.horizon === horizon);
  }

  function cellText(cell: SignalHorizon | undefined): string {
    if (!cell || cell.evidence === "pending") return "Pending";
    return pct(cell.mean_daily_alpha, 2);
  }

  function cellTitle(row: RebuiltArenaPortfolio, cell: SignalHorizon | undefined, horizon: number): string {
    if (!cell) return `${row.name}, ${horizon}-session horizon: pending`;
    const lower = pct(cell.ci_lower, 2);
    const upper = pct(cell.ci_upper, 2);
    return `${row.name}, ${horizon}-session horizon: ${cellText(cell)} mean daily alpha; 95% interval ${lower} to ${upper}; ${cell.evidence}`;
  }

  function detailHref(row: RebuiltArenaPortfolio): string {
    return portfolioAnalysisHref(row.slug, "rebuilt", row.direction, {
      ...context,
      view: "signal",
      objective: "canonical",
      cost_basis: "gross",
      horizon: selectedHorizon,
    });
  }
</script>

<section class="matrix-section" aria-labelledby="signal-matrix-title">
  <header class="section-head">
    <div>
      <h2 id="signal-matrix-title">Signal Alpha matrix</h2>
      <p>
        Mean daily alpha for every completed holding period. The selected {selectedHorizon}-session horizon is
        outlined.
      </p>
    </div>
  </header>

  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <div class="table-scroll" role="region" aria-labelledby="signal-matrix-title" tabindex="0">
    <table class="matrix-table">
      <caption class="visually-hidden">
        Portfolio rows by one through twenty trading-session holding periods. Every cell contains its numeric
        result or pending state.
      </caption>
      <thead>
        <tr>
          <th class="portfolio-head" scope="col">Portfolio</th>
          {#each horizons as horizon (horizon)}
            <th scope="col" class={{ selected: horizon === selectedHorizon }}>
              H{horizon}
            </th>
          {/each}
        </tr>
      </thead>
      <tbody>
        <tr class="benchmark">
          <th scope="row">{benchmarkName} reference</th>
          {#each horizons as horizon (horizon)}
            <td class={{ selected: horizon === selectedHorizon }}>0.00%</td>
          {/each}
        </tr>
        {#each rows as row (row.id)}
          <tr>
            <th scope="row">
              <a href={detailHref(row)} onclick={(event) => link(event, detailHref(row))}>
                {row.name}
              </a>
            </th>
            {#each horizons as horizon (horizon)}
              {@const cell = cellFor(row, horizon)}
              <td
                class={[cell?.evidence ?? "pending", horizon === selectedHorizon && "selected"]}
                title={cellTitle(row, cell, horizon)}
              >
                {cellText(cell)}
              </td>
            {/each}
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
</section>

<style>
  .matrix-section {
    margin-top: 4px;
  }

  .matrix-table {
    --matrix-table-min: 1740px;
    --matrix-cell-width: 74px;
    --matrix-label-width: 236px;
  }

  .matrix-table tr > :first-child {
    padding-left: 12px;
    text-align: left;
  }

  .matrix-table tbody th {
    font-size: 11px;
    font-weight: 650;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .selected {
    box-shadow:
      inset 2px 0 var(--accent),
      inset -2px 0 var(--accent);
  }

  .matrix-table thead .selected {
    color: var(--accent);
    box-shadow:
      inset 2px 0 var(--accent),
      inset -2px 0 var(--accent),
      inset 0 2px var(--accent);
  }

  .matrix-table tbody tr:last-child .selected {
    box-shadow:
      inset 2px 0 var(--accent),
      inset -2px 0 var(--accent),
      inset 0 -2px var(--accent);
  }

  .matrix-table .benchmark th,
  .matrix-table .benchmark td {
    color: var(--text-tertiary);
    background: var(--bg-inset);
  }

  .benchmark .selected {
    color: var(--accent);
  }
</style>
