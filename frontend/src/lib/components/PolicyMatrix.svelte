<script lang="ts">
  import type { PolicyMatrixCell, RebuiltPolicy } from "../api/types";
  import { pct } from "../format";

  interface Props {
    cells: PolicyMatrixCell[];
    selected: RebuiltPolicy | null;
  }

  const { cells, selected }: Props = $props();
  const horizons = Array.from({ length: 20 }, (_, index) => index + 1);
  const exposures = Array.from({ length: 10 }, (_, index) => (index + 1) * 10);

  function cellFor(horizon: number, exposure: number): PolicyMatrixCell | undefined {
    return cells.find((cell) => cell.horizon === horizon && cell.exposure_pct === exposure);
  }

  function textFor(cell: PolicyMatrixCell | undefined): string {
    if (!cell?.metrics.has_data) return "Pending";
    return pct(cell.metrics.mean_daily_alpha, 2);
  }

  function titleFor(cell: PolicyMatrixCell | undefined, horizon: number, exposure: number): string {
    if (!cell) return `H${horizon}, ${exposure}% exposure: pending`;
    return `H${horizon}, ${exposure}% exposure: ${textFor(cell)} mean daily alpha; lower 95% ${pct(cell.metrics.ci_lower, 2)}; ${cell.metrics.evidence ?? "pending"}`;
  }
</script>

<section class="matrix-section" aria-labelledby="policy-matrix-title">
  <header>
    <h2 id="policy-matrix-title">Policy matrix</h2>
    <p>Mean daily alpha for every holding-period and total-exposure pair.</p>
  </header>
  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <div class="matrix-scroll" role="region" aria-labelledby="policy-matrix-title" tabindex="0">
    <table>
      <caption>
        Holding-period rows by ten through one hundred percent exposure columns. Every cell contains its
        numeric result or pending state.
      </caption>
      <thead>
        <tr>
          <th scope="col">Horizon</th>
          {#each exposures as exposure (exposure)}
            <th scope="col">{exposure}%</th>
          {/each}
        </tr>
      </thead>
      <tbody>
        {#each horizons as horizon (horizon)}
          <tr>
            <th scope="row">H{horizon}</th>
            {#each exposures as exposure (exposure)}
              {@const cell = cellFor(horizon, exposure)}
              <td
                class={[
                  cell?.metrics.evidence ?? "pending",
                  selected?.horizon === horizon && selected.exposure_pct === exposure && "selected",
                ]}
                title={titleFor(cell, horizon, exposure)}
              >
                {textFor(cell)}
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
    min-width: 0;
    display: grid;
    gap: 12px;
  }

  header {
    padding-bottom: 10px;
    border-bottom: 1px solid var(--border-subtle);
  }

  h2 {
    margin: 0;
    font-size: 16px;
  }

  p {
    margin-top: 5px;
    color: var(--text-secondary);
    font-size: 11px;
  }

  .matrix-scroll {
    overflow-x: auto;
    border: 1px solid var(--border-subtle);
  }

  table {
    min-width: 980px;
    table-layout: fixed;
  }

  caption {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
    border: 0;
  }

  th,
  td {
    width: 88px;
    padding: 8px 7px;
    border-right: 1px solid var(--border-subtle);
    font-family: var(--font-mono);
    font-size: 10px;
    font-variant-numeric: tabular-nums;
    text-align: center;
    white-space: nowrap;
  }

  th:first-child {
    position: sticky;
    left: 0;
    z-index: 2;
    width: 80px;
    background: var(--bg-surface);
  }

  td.positive {
    color: var(--pos);
    background: color-mix(in srgb, var(--pos) 9%, transparent);
  }

  td.negative {
    color: var(--neg);
    background: color-mix(in srgb, var(--neg) 9%, transparent);
  }

  td.inconclusive {
    color: var(--warn);
    background: var(--warn-bg);
  }

  td.pending {
    color: var(--text-tertiary);
    font-family: var(--font-sans);
    font-size: 9px;
  }

  td.selected {
    box-shadow: inset 0 0 0 2px var(--accent);
  }
</style>
