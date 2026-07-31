<script lang="ts">
  import type { PortfolioRefOut } from "../api/types";
  import { portfolioAnalysisHref } from "../arena";
  import { link } from "../stores/router.svelte";

  interface Props {
    rows: PortfolioRefOut[];
  }

  const { rows }: Props = $props();

  function portfolioHref(row: PortfolioRefOut): string {
    return portfolioAnalysisHref(row.slug, row.prompt_mode, row.direction);
  }
</script>

<div class="table-scroll">
  <table>
    <caption>Portfolios associated with this record</caption>
    <thead>
      <tr><th>Portfolio</th><th>Direction</th><th>Track</th><th>Status</th><th></th></tr>
    </thead>
    <tbody>
      {#each rows as row (row.id)}
        <tr>
          <th scope="row">
            <a href={portfolioHref(row)} onclick={(event) => link(event, portfolioHref(row))}>{row.name}</a>
          </th>
          <td><span class="badge">{row.direction}</span></td>
          <td><span class="badge">{row.prompt_mode}</span></td>
          <td>
            {row.status}
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
        <tr><td colspan="5" class="muted">No portfolios use this record.</td></tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .table-scroll {
    overflow-x: auto;
    border: 1px solid var(--border-subtle);
  }

  table {
    min-width: 560px;
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

  th[scope="row"] {
    text-align: left;
  }

  th[scope="row"] a {
    font-weight: 700;
  }
</style>
