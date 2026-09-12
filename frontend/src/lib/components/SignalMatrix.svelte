<script lang="ts">
  import type { RebuiltArenaPortfolio, SignalHorizon } from "../api/types";
  import { portfolioAnalysisHref } from "../arena";
  import { pct, pctSignClass } from "../format";
  import { link } from "../stores/router.svelte";

  interface Props {
    rows: RebuiltArenaPortfolio[];
    benchmarkName: string;
  }

  const { rows, benchmarkName }: Props = $props();
  const horizons = Array.from({ length: 40 }, (_, index) => (index + 1) / 2);
  const maxMagnitude = $derived.by(() => {
    let maximum = 0;
    for (const row of rows) {
      for (const cell of row.signal_horizons) {
        maximum = Math.max(maximum, Math.abs(cellValue(cell) ?? 0));
      }
    }
    return maximum;
  });

  function cellValue(cell: SignalHorizon | undefined): number | null {
    const value = cell?.mean_daily_alpha;
    if (cell?.evidence === "pending" || value == null || !Number.isFinite(value)) return null;
    // Match the displayed precision so rounded zero stays neutral.
    return Number((value * 100).toFixed(2)) / 100;
  }

  function cellIntensity(value: number | null): string {
    if (value === null || value === 0 || maxMagnitude === 0) return "0%";
    return `${4 + (Math.abs(value) / maxMagnitude) * 36}%`;
  }

  function cellFor(row: RebuiltArenaPortfolio, horizon: number): SignalHorizon | undefined {
    return row.signal_horizons.find((cell) => cell.horizon === horizon);
  }

  function cellText(cell: SignalHorizon | undefined): string {
    const value = cellValue(cell);
    return value === null ? "Pending" : pct(value, 2);
  }

  function cellTitle(row: RebuiltArenaPortfolio, cell: SignalHorizon | undefined, horizon: number): string {
    if (!cell) return `${row.name}, ${horizon}-session horizon: pending`;
    const lower = pct(cell.ci_lower, 2);
    const upper = pct(cell.ci_upper, 2);
    return `${row.name}, ${horizon}-session horizon: ${cellText(cell)} mean daily alpha; 95% interval ${lower} to ${upper}; ${cell.evidence}`;
  }

  function detailHref(row: RebuiltArenaPortfolio): string {
    return portfolioAnalysisHref(
      row.slug,
      "rebuilt",
      row.direction,
      row.version_id,
      row.optimization_objective,
    );
  }
</script>

<section class="matrix-section" aria-labelledby="signal-matrix-title">
  <header class="section-head">
    <div>
      <h2 id="signal-matrix-title">Signal Alpha matrix</h2>
      <p>
        Signal α/day at half-session holding periods. Red is negative; green is positive. Stronger color means
        larger magnitude on one shared scale across all cells in this matrix. Zero is neutral.
      </p>
    </div>
  </header>

  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <div class="table-scroll" role="region" aria-labelledby="signal-matrix-title" tabindex="0">
    <table class="matrix-table">
      <caption class="visually-hidden">
        Portfolio rows by half through twenty trading-session holding periods. Every cell contains its numeric
        result or pending state.
      </caption>
      <thead>
        <tr>
          <th class="portfolio-head" scope="col">Portfolio</th>
          {#each horizons as horizon (horizon)}
            <th scope="col">
              H{horizon}
            </th>
          {/each}
        </tr>
      </thead>
      <tbody>
        <tr class="benchmark">
          <th scope="row">{benchmarkName} reference</th>
          {#each horizons as horizon (horizon)}
            <td>0.00%</td>
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
              {@const value = cellValue(cell)}
              <td
                class={value === null ? "pending" : pctSignClass(value, 2)}
                style:--alpha-intensity={cellIntensity(value)}
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
    --matrix-table-min: 3240px;
    --matrix-cell-width: 74px;
    --matrix-label-width: 236px;
  }

  .matrix-table tr > :first-child {
    padding-left: 12px;
    text-align: left;
  }

  .matrix-table tr > :first-child {
    position: sticky;
    left: 0;
    z-index: 1;
    background: var(--bg-surface);
  }

  .matrix-table tbody th {
    font-size: 11px;
    font-weight: 650;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .matrix-table td.pos,
  .matrix-table td.neg {
    color: var(--text-primary);
    background: color-mix(in srgb, var(--alpha-color) var(--alpha-intensity), var(--bg-surface));
  }

  .matrix-table td.pos {
    --alpha-color: var(--pos);
  }

  .matrix-table td.neg {
    --alpha-color: var(--neg);
  }

  .matrix-table .benchmark th,
  .matrix-table .benchmark td {
    color: var(--text-tertiary);
    background: var(--bg-inset);
  }
</style>
