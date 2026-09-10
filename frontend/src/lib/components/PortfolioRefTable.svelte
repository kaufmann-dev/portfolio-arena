<script lang="ts">
  import type { PortfolioRefOut } from "../api/types";
  import { portfolioAnalysisHref } from "../arena";
  import { link } from "../stores/router.svelte";

  interface Props {
    rows: PortfolioRefOut[];
  }

  const { rows }: Props = $props();

  function portfolioHref(row: PortfolioRefOut): string {
    return portfolioAnalysisHref(row.slug, row.prompt_mode, row.direction, row.version_id);
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div class="table-scroll" role="region" aria-label="Portfolios associated with this record" tabindex="0">
  <table class="data-table">
    <caption class="visually-hidden">Portfolios associated with this record</caption>
    <thead>
      <tr>
        <th scope="col">Portfolio</th>
        <th scope="col">Direction</th>
        <th scope="col">Track</th>
        <th scope="col">Version</th>
        <th scope="col">Execution</th>
        <th scope="col"><span class="visually-hidden">Actions</span></th>
      </tr>
    </thead>
    <tbody>
      {#each rows as row (row.id)}
        <tr>
          <th scope="row">
            <a href={portfolioHref(row)} onclick={(event) => link(event, portfolioHref(row))}>{row.name}</a>
          </th>
          <td><span class="badge">{row.direction}</span></td>
          <td><span class="badge">{row.prompt_mode}</span></td>
          <td><span class="badge">{row.version.name}</span></td>
          <td>
            {row.execution_boundary === "open" ? "Open" : "Close"}
            {#if row.is_liquidated}
              <span class="badge neg"
                >{row.prompt_mode === "rebuilt" ? "policy liquidated" : "liquidated"}</span
              >
            {/if}
          </td>
          <td class="right">
            <a href={portfolioHref(row)} onclick={(event) => link(event, portfolioHref(row))}>Open →</a>
          </td>
        </tr>
      {:else}
        <tr><td colspan="6" class="table-empty">No portfolios use this record.</td></tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  table {
    min-width: 640px;
  }

  th[scope="row"] a {
    font-weight: 700;
  }
</style>
