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
  <header class="section-head">
    <div>
      <h2 id="policy-matrix-title">Policy matrix</h2>
      <p>Mean daily alpha for every holding-period and total-exposure pair.</p>
    </div>
  </header>
  <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
  <div class="table-scroll" role="region" aria-labelledby="policy-matrix-title" tabindex="0">
    <table class="matrix-table">
      <caption class="visually-hidden">
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
  .matrix-table {
    --matrix-table-min: 980px;
    --matrix-cell-width: 88px;
    --matrix-label-width: 80px;
  }

  .matrix-table td.selected {
    box-shadow: inset 0 0 0 2px var(--accent);
  }
</style>
